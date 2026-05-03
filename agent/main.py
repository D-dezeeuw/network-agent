import os

from apscheduler.schedulers.blocking import BlockingScheduler

from config import REPORT_HOUR, RESET_BASELINE
from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from logs import get_auth_log_summary
from security_news import fetch_security_news
from security_scan import run_scan
from system_health import run_health_check
from ai import generate_report
from telegram import send_message


def run_agent():
    print("[agent] Starting daily report collection...")

    raw_metrics = collect_all_metrics()
    metrics_summary = {name: summarize_chart(data) for name, data in raw_metrics.items()}
    metrics_summary["active_alarms"] = fetch_active_alarms()

    logs = get_auth_log_summary(hours=24)
    news = fetch_security_news()
    security = run_scan(reset=RESET_BASELINE)
    health = run_health_check()

    report = generate_report(metrics_summary, logs, news, security, health)

    success = send_message(report)
    print(f"[agent] Report sent: {success}")


if __name__ == "__main__":
    if os.getenv("RUN_NOW", "false").lower() == "true":
        run_agent()
    else:
        scheduler = BlockingScheduler()
        scheduler.add_job(run_agent, "cron", hour=REPORT_HOUR, minute=0)
        print(f"[agent] Scheduled daily at {REPORT_HOUR}:00")

        run_agent()

        scheduler.start()
