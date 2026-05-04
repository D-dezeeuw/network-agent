import json

import ignored


def _redirect(monkeypatch, tmp_path):
    p = tmp_path / "ignored.json"
    monkeypatch.setattr(ignored, "IGNORED_PATH", str(p))
    monkeypatch.setattr(ignored, "STATE_DIR", str(tmp_path))
    return p


def test_load_returns_empty_when_file_missing(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ignored.ignored_entries() == {}
    assert ignored.ignored_fingerprints() == set()


def test_add_persists_and_returns_entry(monkeypatch, tmp_path):
    p = _redirect(monkeypatch, tmp_path)
    entry = ignored.add_ignored("abc12345", "🔧 evil cron at /tmp/x")
    assert entry["fingerprint"] == "abc12345"
    assert entry["label"].startswith("🔧 evil cron")
    assert "added_at" in entry
    raw = json.loads(p.read_text())
    assert "abc12345" in raw


def test_add_truncates_long_labels(monkeypatch, tmp_path):
    """Defensive cap matches acks behavior — Telegram message text can be huge."""
    _redirect(monkeypatch, tmp_path)
    entry = ignored.add_ignored("abc12345", "x" * 1000)
    assert len(entry["label"]) == 200


def test_add_is_idempotent_overwrites_label(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    ignored.add_ignored("abc12345", "first label")
    ignored.add_ignored("abc12345", "second label")
    entries = ignored.ignored_entries()
    assert len(entries) == 1
    assert entries["abc12345"]["label"] == "second label"


def test_is_ignored_reflects_storage(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ignored.is_ignored("abc12345") is False
    ignored.add_ignored("abc12345", "x")
    assert ignored.is_ignored("abc12345") is True


def test_ignored_fingerprints_returns_set(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    ignored.add_ignored("abc12345", "x")
    ignored.add_ignored("def67890", "y")
    assert ignored.ignored_fingerprints() == {"abc12345", "def67890"}


def test_remove_returns_true_when_present(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    ignored.add_ignored("abc12345", "x")
    assert ignored.remove_ignored("abc12345") is True
    assert ignored.ignored_fingerprints() == set()


def test_remove_returns_false_when_absent(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert ignored.remove_ignored("abc12345") is False


def test_persistence_across_invocations(monkeypatch, tmp_path):
    """Ignored entries must survive process restart — that's the whole point."""
    _redirect(monkeypatch, tmp_path)
    ignored.add_ignored("abc12345", "persistent thing")
    # New "process" → re-load from disk
    assert "abc12345" in ignored.ignored_entries()
