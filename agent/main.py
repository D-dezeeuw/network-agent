import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from acks import snoozed_fingerprints
from config import REPORT_HOUR, REPORT_INTERVAL_HOURS, RESET_BASELINE
from findings import enumerate_findings, filter_unsnoozed, strip_snoozed_from_data
from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from logs import get_auth_log_summary
from notifications import (
    alarm_poller_loop,
    is_muted,
    send_to_critical,
    should_send_digest,
)
from security_news import fetch_security_news
from security_scan import run_scan
from system_health import run_health_check
from trends import (
    all_disk_forecasts,
    compute_deltas,
    extract_snapshot,
    load_recent,
    metric_series,
    previous_snapshot,
    prune_snapshots,
    save_snapshot,
)
from ai import generate_report
from charts import render_sparkline, render_status_grid
from tg_publish import send_message_with_buttons, send_messages, send_photo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("agent")


def run_agent() -> None:
    log.info("Starting report collection")

    raw_metrics = collect_all_metrics()
    metrics_summary = {}
    for name, data in raw_metrics.items():
        if "data" in data:
            metrics_summary[name] = summarize_chart(data)
    metrics_summary["active_alarms"] = fetch_active_alarms()

    logs = get_auth_log_summary(hours=24)
    news = fetch_security_news()
    security = run_scan(reset=RESET_BASELINE)
    health = run_health_check()

    snoozed = snoozed_fingerprints()
    sec_filtered, health_filtered = strip_snoozed_from_data(security, health, snoozed)
    active_findings = filter_unsnoozed(enumerate_findings(security, health), snoozed)
    log.info("findings: %d active, %d snoozed", len(active_findings), len(snoozed))

    # Trends: compute deltas vs an earlier snapshot, then save current + prune.
    current_snap = extract_snapshot(metrics_summary, health)
    history = load_recent()
    deltas = compute_deltas(current_snap, previous_snapshot(history + [current_snap]))
    forecasts = all_disk_forecasts(history + [current_snap])
    trends = {"deltas": deltas, "disk_forecasts": forecasts} if (deltas or forecasts) else {}
    log.info("trends: %d deltas, %d forecasts", len(deltas), len(forecasts))
    save_snapshot(current_snap)
    prune_snapshots()

    parts = generate_report(metrics_summary, logs, news, sec_filtered, health_filtered, trends)

    # Snapshots/baselines always update so history stays continuous; the
    # gates below only suppress *outgoing* messages.
    allow_digest, reason = should_send_digest()
    if allow_digest:
        success = send_messages(parts)
        log.info("Report sent: %d parts, ok=%s", len(parts), success)
        _send_digest_charts(history + [current_snap], health_filtered)
        _send_finding_buttons(active_findings)
    else:
        log.info("Routine digest suppressed (%s); criticals still route", reason)

    # Critical findings always go to the critical chat (when configured),
    # bypassing quiet hours but respecting mute. send_to_critical() handles
    # both checks internally.
    _route_critical_findings(active_findings)


def _send_digest_charts(snapshots: list[dict], health: dict) -> None:
    """Append visual charts after the text digest.

    Sends a status grid (containers + disk usage) always, plus per-metric
    sparklines for CPU and RAM if there are at least 2 snapshots to plot.
    Each chart is rendered once per cycle — no in-process cache needed
    because run_agent is short-lived per invocation.
    """
    cache: dict[str, bytes] = {}

    def _render(key: str, fn) -> bytes | None:
        if key in cache:
            return cache[key]
        try:
            blob = fn()
            cache[key] = blob
            return blob
        except Exception as e:
            log.warning("chart render failed for %s: %s", key, e)
            return None

    docker = (health or {}).get("docker_containers") or {}
    containers = docker.get("all_containers") or docker.get("concerning") or []
    disks = {}
    if snapshots:
        disks = (snapshots[-1].get("disks") or {})

    grid = _render("status_grid",
                   lambda: render_status_grid(containers, disks))
    if grid:
        send_photo(grid, caption="<b>System status</b>")

    for metric_key, label in (("cpu_avg", "CPU avg %"), ("ram_avg", "RAM avg %")):
        series = metric_series(snapshots, metric_key)
        if len(series) < 2:
            continue
        png = _render(metric_key, lambda s=series, l=label: render_sparkline(s, l))
        if png:
            send_photo(png)


def _send_finding_buttons(findings) -> None:
    """For each unsnoozed finding, send a follow-up message with snooze buttons."""
    for f in findings:
        buttons = [[
            ("Snooze 24h", f"ack:s24:{f.fingerprint}"),
            ("Snooze 7d", f"ack:s7d:{f.fingerprint}"),
            ("Investigate", f"ack:inv:{f.fingerprint}"),
        ]]
        text = f"{f.label}\n<i>id: <code>{f.fingerprint}</code></i>"
        send_message_with_buttons(text, buttons)


def _route_critical_findings(findings) -> None:
    """Mirror critical-severity findings to the critical chat (text-only,
    no buttons — that interactive surface stays in the digest chat)."""
    for f in findings:
        if f.severity == "critical":
            send_to_critical(f.label)


def _build_trigger():
    if REPORT_INTERVAL_HOURS:
        log.info("Scheduling agent every %sh", REPORT_INTERVAL_HOURS)
        return IntervalTrigger(hours=REPORT_INTERVAL_HOURS)
    log.info("Scheduling agent daily at %02d:00", REPORT_HOUR)
    return CronTrigger(hour=REPORT_HOUR, minute=0)


async def main_async() -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_agent, _build_trigger(), id="digest")
    scheduler.start()

    # Run one digest immediately so a fresh deploy produces output.
    await asyncio.to_thread(run_agent)

    # Real-time critical-alarm poller runs alongside everything else.
    # Idles itself if TELEGRAM_CRITICAL_CHAT_ID isn't set.
    poller_task = asyncio.create_task(alarm_poller_loop(), name="alarm-poller")

    # Lazy import to keep main.py importable from bot.py at handler time.
    from bot import build_application, register_commands
    bot_app = build_application()

    if bot_app is None:
        log.info("Q&A bot not configured; running digest scheduler + alarm poller")
        try:
            await asyncio.Event().wait()
        finally:
            poller_task.cancel()
        return

    log.info("Starting Telegram polling")
    async with bot_app:
        await register_commands(bot_app)
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()
        finally:
            poller_task.cancel()
            await bot_app.updater.stop()
            await bot_app.stop()


def main() -> None:
    if os.getenv("RUN_NOW", "false").lower() == "true":
        run_agent()
        return
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
