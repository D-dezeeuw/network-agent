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
from security_news import fetch_security_news
from security_scan import run_scan
from system_health import run_health_check
from ai import generate_report
from tg_publish import send_message_with_buttons, send_messages

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

    parts = generate_report(metrics_summary, logs, news, sec_filtered, health_filtered)
    success = send_messages(parts)
    log.info("Report sent: %d parts, ok=%s", len(parts), success)

    _send_finding_buttons(active_findings)


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

    # Lazy import to keep main.py importable from bot.py at handler time.
    from bot import build_application
    bot_app = build_application()

    if bot_app is None:
        log.info("Q&A bot not configured; running digest scheduler only")
        # Block forever so the container stays up.
        await asyncio.Event().wait()
        return

    log.info("Starting Telegram polling")
    async with bot_app:
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()
        finally:
            await bot_app.updater.stop()
            await bot_app.stop()


def main() -> None:
    if os.getenv("RUN_NOW", "false").lower() == "true":
        run_agent()
        return
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
