from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import main


def test_build_trigger_defaults_to_cron(monkeypatch):
    monkeypatch.setattr(main, "REPORT_INTERVAL_HOURS", None)
    monkeypatch.setattr(main, "REPORT_HOUR", 8)
    trigger = main._build_trigger()
    assert isinstance(trigger, CronTrigger)


def test_build_trigger_uses_interval_when_set(monkeypatch):
    monkeypatch.setattr(main, "REPORT_INTERVAL_HOURS", 6)
    trigger = main._build_trigger()
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval.total_seconds() == 6 * 3600
