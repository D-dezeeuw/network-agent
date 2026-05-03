import json
from datetime import datetime, timedelta, timezone

import notifications


def _set_mute_path(monkeypatch, tmp_path):
    p = tmp_path / "mute.json"
    monkeypatch.setattr(notifications, "MUTE_PATH", str(p))
    monkeypatch.setattr(notifications, "STATE_DIR", str(tmp_path))
    return p


# --- quiet hours -------------------------------------------------------------

def test_parse_quiet_hours_simple():
    assert notifications.parse_quiet_hours("22-7") == (22, 7)
    assert notifications.parse_quiet_hours("9-17") == (9, 17)


def test_parse_quiet_hours_empty_returns_none():
    assert notifications.parse_quiet_hours("") is None
    assert notifications.parse_quiet_hours(None) is None


def test_parse_quiet_hours_malformed_returns_none():
    assert notifications.parse_quiet_hours("garbage") is None
    assert notifications.parse_quiet_hours("8") is None
    assert notifications.parse_quiet_hours("a-b") is None


def test_is_quiet_at_simple_window():
    """9-17 → quiet during business hours."""
    assert notifications.is_quiet_at(10, "9-17") is True
    assert notifications.is_quiet_at(8, "9-17") is False
    assert notifications.is_quiet_at(17, "9-17") is False  # exclusive end
    assert notifications.is_quiet_at(16, "9-17") is True


def test_is_quiet_at_midnight_wrap():
    """22-7 wraps midnight: quiet 22..23 and 0..6."""
    assert notifications.is_quiet_at(22, "22-7") is True
    assert notifications.is_quiet_at(23, "22-7") is True
    assert notifications.is_quiet_at(0, "22-7") is True
    assert notifications.is_quiet_at(6, "22-7") is True
    assert notifications.is_quiet_at(7, "22-7") is False
    assert notifications.is_quiet_at(12, "22-7") is False
    assert notifications.is_quiet_at(21, "22-7") is False


def test_is_quiet_at_no_window():
    """Empty/unset → never quiet."""
    assert notifications.is_quiet_at(3, "") is False
    assert notifications.is_quiet_at(3, None) is False


def test_is_quiet_at_zero_width_window_is_never_quiet():
    """start == end is treated as 'no window' to avoid full-day silence."""
    assert notifications.is_quiet_at(5, "5-5") is False


# --- mute persistence --------------------------------------------------------

def test_is_muted_false_when_no_file(monkeypatch, tmp_path):
    _set_mute_path(monkeypatch, tmp_path)
    assert notifications.is_muted() is False


def test_mute_for_persists_and_is_active(monkeypatch, tmp_path):
    p = _set_mute_path(monkeypatch, tmp_path)
    record = notifications.mute_for(2)
    assert "expires_at" in record
    assert notifications.is_muted() is True
    raw = json.loads(p.read_text())
    assert raw["expires_at"] == record["expires_at"]


def test_clear_mute_removes_file(monkeypatch, tmp_path):
    _set_mute_path(monkeypatch, tmp_path)
    notifications.mute_for(1)
    assert notifications.clear_mute() is True
    assert notifications.is_muted() is False
    # Idempotent
    assert notifications.clear_mute() is False


def test_expired_mute_is_not_active(monkeypatch, tmp_path):
    p = _set_mute_path(monkeypatch, tmp_path)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    p.write_text(json.dumps({"expires_at": past.isoformat()}))
    assert notifications.is_muted() is False


def test_corrupt_mute_file_treated_as_unmuted(monkeypatch, tmp_path):
    p = _set_mute_path(monkeypatch, tmp_path)
    p.write_text("{not valid json")
    assert notifications.is_muted() is False


def test_mute_status_returns_record_when_active(monkeypatch, tmp_path):
    _set_mute_path(monkeypatch, tmp_path)
    notifications.mute_for(2)
    status = notifications.mute_status()
    assert status is not None
    assert "expires_at" in status


def test_mute_status_returns_none_when_expired(monkeypatch, tmp_path):
    p = _set_mute_path(monkeypatch, tmp_path)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    p.write_text(json.dumps({"expires_at": past.isoformat()}))
    assert notifications.mute_status() is None


# --- routing decisions -------------------------------------------------------

def test_should_send_digest_true_when_clean(monkeypatch, tmp_path):
    _set_mute_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notifications, "QUIET_HOURS", "")
    allow, reason = notifications.should_send_digest()
    assert allow is True
    assert reason == "ok"


def test_should_send_digest_false_when_muted(monkeypatch, tmp_path):
    _set_mute_path(monkeypatch, tmp_path)
    notifications.mute_for(1)
    allow, reason = notifications.should_send_digest()
    assert allow is False
    assert reason == "muted"


# --- alarm poller logic ------------------------------------------------------

def _alarm(name="cpu_high", chart="system.cpu", status="CRITICAL", value=95):
    return {"name": name, "chart": chart, "status": status, "value": value, "units": "%"}


def test_alarm_fingerprint_stable():
    a = _alarm()
    assert notifications._alarm_fingerprint(a) == notifications._alarm_fingerprint(a)


def test_alarm_fingerprint_includes_status():
    """A WARNING and CRITICAL of the same alarm hash differently — clearing
    a CRITICAL and re-firing should be a new event."""
    a = _alarm(status="CRITICAL")
    b = _alarm(status="WARNING")
    assert notifications._alarm_fingerprint(a) != notifications._alarm_fingerprint(b)


def test_select_alarms_filters_non_critical():
    alarms = [_alarm(status="WARNING"), _alarm(status="CRITICAL", name="x"),
              _alarm(status="CLEAR", name="y")]
    out = notifications.select_alarms_to_send(alarms, {}, datetime.now(timezone.utc))
    assert len(out) == 1
    assert out[0][1]["name"] == "x"


def test_select_alarms_skips_seen():
    alarms = [_alarm(name="cpu_high"), _alarm(name="ram_high")]
    seen = {notifications._alarm_fingerprint(alarms[0]): datetime.now(timezone.utc)}
    out = notifications.select_alarms_to_send(alarms, seen, datetime.now(timezone.utc))
    assert len(out) == 1
    assert out[0][1]["name"] == "ram_high"


def test_select_alarms_empty_input():
    assert notifications.select_alarms_to_send([], {}, datetime.now(timezone.utc)) == []
    assert notifications.select_alarms_to_send(None, {}, datetime.now(timezone.utc)) == []


def test_prune_seen_removes_old_entries():
    now = datetime.now(timezone.utc)
    seen = {
        "fresh": now - timedelta(seconds=60),
        "stale": now - timedelta(seconds=notifications.ALARM_THROTTLE_S + 60),
    }
    out = notifications.prune_seen(seen, now)
    assert "fresh" in out
    assert "stale" not in out


def test_format_alarm_includes_key_fields():
    formatted = notifications._format_alarm(_alarm(name="loadavg_high", value=4.5,
                                                   status="CRITICAL"))
    assert "loadavg_high" in formatted
    assert "CRITICAL" in formatted
    assert "4.5" in formatted


def test_format_alarm_handles_minimal_fields():
    """Should never crash on partial alarm dicts."""
    formatted = notifications._format_alarm({"name": "x", "status": "CRITICAL"})
    assert "x" in formatted
    assert "CRITICAL" in formatted


def test_is_critical_handles_both_spellings():
    assert notifications._is_critical({"status": "CRITICAL"}) is True
    assert notifications._is_critical({"status": "CRIT"}) is True
    assert notifications._is_critical({"status": "WARNING"}) is False
    assert notifications._is_critical({"status": "CLEAR"}) is False
    assert notifications._is_critical({}) is False
