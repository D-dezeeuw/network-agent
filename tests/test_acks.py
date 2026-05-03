import json
from datetime import datetime, timedelta, timezone

import acks


def _set_state_dir(monkeypatch, tmp_path):
    """Point ACKS_PATH at a temp file for the duration of a test."""
    p = tmp_path / "acks.json"
    monkeypatch.setattr(acks, "ACKS_PATH", str(p))
    return p


def test_fingerprint_is_stable():
    assert acks.fingerprint("cron_new", "/etc/cron.d/foo") \
        == acks.fingerprint("cron_new", "/etc/cron.d/foo")


def test_fingerprint_differs_per_input():
    a = acks.fingerprint("cron_new", "/etc/cron.d/foo")
    b = acks.fingerprint("cron_new", "/etc/cron.d/bar")
    c = acks.fingerprint("cron_modified", "/etc/cron.d/foo")
    assert a != b and a != c and b != c


def test_fingerprint_short_for_callback_data():
    fp = acks.fingerprint("cron_new", "/very/long/path/that/keeps/going")
    assert len(fp) <= 16  # well under Telegram's 64-byte callback_data limit


def test_add_ack_persists(monkeypatch, tmp_path):
    _set_state_dir(monkeypatch, tmp_path)
    fp = acks.fingerprint("cron_new", "/etc/cron.d/foo")
    acks.add_ack(fp, "test label", hours=24)
    assert acks.is_snoozed(fp)
    # Reload from disk
    raw = json.loads(open(acks.ACKS_PATH).read())
    assert fp in raw
    assert raw[fp]["label"] == "test label"


def test_remove_ack(monkeypatch, tmp_path):
    _set_state_dir(monkeypatch, tmp_path)
    fp = acks.fingerprint("x", "y")
    acks.add_ack(fp, "to remove", hours=24)
    assert acks.remove_ack(fp) is True
    assert acks.is_snoozed(fp) is False
    # Removing twice is a no-op
    assert acks.remove_ack(fp) is False


def test_expired_acks_pruned_on_read(monkeypatch, tmp_path):
    p = _set_state_dir(monkeypatch, tmp_path)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    p.write_text(json.dumps({
        "expired1": {"fingerprint": "expired1", "label": "old",
                     "added_at": past.isoformat(),
                     "expires_at": past.isoformat()},
    }))
    assert acks.active_acks() == {}
    # Pruning persisted to disk
    assert json.loads(p.read_text()) == {}


def test_active_acks_keeps_unexpired(monkeypatch, tmp_path):
    _set_state_dir(monkeypatch, tmp_path)
    fp = acks.fingerprint("a", "b")
    acks.add_ack(fp, "still good", hours=24)
    active = acks.active_acks()
    assert fp in active
    assert active[fp]["label"] == "still good"


def test_snoozed_fingerprints_returns_set(monkeypatch, tmp_path):
    _set_state_dir(monkeypatch, tmp_path)
    fp1 = acks.fingerprint("a", "1")
    fp2 = acks.fingerprint("a", "2")
    acks.add_ack(fp1, "x", hours=1)
    acks.add_ack(fp2, "y", hours=1)
    s = acks.snoozed_fingerprints()
    assert isinstance(s, set)
    assert s == {fp1, fp2}


def test_corrupt_acks_file_returns_empty(monkeypatch, tmp_path):
    p = _set_state_dir(monkeypatch, tmp_path)
    p.write_text("{not valid json")
    assert acks.active_acks() == {}
