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


# --- _trim_health_for_ai -----------------------------------------------------

def test_trim_health_for_ai_drops_all_containers():
    """The AI should never see the healthy-container inventory — it tempts
    the model to enumerate them in the digest."""
    health = {
        "docker_containers": {
            "concerning": [{"name": "plex"}],
            "all_containers": [
                {"name": "plex", "status": "running"},
                {"name": "nginx", "status": "running"},
            ],
        },
        "reboot_required": {"required": False},
    }
    out = main._trim_health_for_ai(health)
    assert "all_containers" not in out["docker_containers"]
    assert out["docker_containers"]["concerning"] == [{"name": "plex"}]
    assert out["reboot_required"] == {"required": False}


def test_trim_health_for_ai_does_not_mutate_input():
    health = {"docker_containers": {"all_containers": [1, 2, 3]}}
    out = main._trim_health_for_ai(health)
    assert "all_containers" in health["docker_containers"]
    assert "all_containers" not in out["docker_containers"]


def test_trim_health_for_ai_handles_missing_docker():
    """If docker_containers isn't there (e.g. muted), don't crash."""
    health = {"reboot_required": {"required": True}}
    out = main._trim_health_for_ai(health)
    assert out == health
    assert out is not health  # still deep-copied


def test_trim_health_for_ai_handles_non_dict_input():
    """Defensive — if upstream returns something weird, pass through."""
    assert main._trim_health_for_ai(None) is None
    assert main._trim_health_for_ai("muted") == "muted"
