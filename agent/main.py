import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import REPORT_HOUR, REPORT_INTERVAL_HOURS, RESET_BASELINE
from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from logs import get_auth_log_summary
from security_news import fetch_security_news
from security_scan import run_scan
from system_health import run_health_check
from ai import generate_report
from tg_publish import send_message

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

    report = generate_report(metrics_summary, logs, news, security, health)
    success = send_message(report)
    log.info("Report sent: %s", success)


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
