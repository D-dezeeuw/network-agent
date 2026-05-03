import json

import tool_mute


def _set_path(monkeypatch, tmp_path):
    p = tmp_path / "tool_mute.json"
    monkeypatch.setattr(tool_mute, "MUTE_PATH", str(p))
    return p


def test_resolve_known_alias():
    assert tool_mute.resolve("news") == "news"
    assert tool_mute.resolve("scan") == "security_scan"
    assert tool_mute.resolve("health") == "system_health"


def test_resolve_normalizes_case_and_whitespace():
    assert tool_mute.resolve("  News ") == "news"
    assert tool_mute.resolve("DOCKER") == "docker"


def test_resolve_unknown_returns_none():
    assert tool_mute.resolve("nonsense") is None


def test_known_aliases_returns_sorted_list():
    aliases = tool_mute.known_aliases()
    assert sorted(aliases) == aliases
    assert "news" in aliases


def test_mute_indefinite(monkeypatch, tmp_path):
    p = _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=None)
    assert tool_mute.is_muted("news") is True
    assert json.loads(p.read_text())["news"] is None


def test_mute_with_count(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=3)
    assert tool_mute.is_muted("news") is True


def test_decrement_counts_decrements_and_keeps_indefinite(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=3)
    tool_mute.mute("docker", cycles=None)
    expired = tool_mute.decrement_counts()
    assert expired == 0
    state = tool_mute.active_mutes()
    assert state["news"] == 2
    assert state["docker"] is None


def test_decrement_counts_removes_at_zero(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=1)
    expired = tool_mute.decrement_counts()
    assert expired == 1
    assert tool_mute.is_muted("news") is False


def test_decrement_counts_handles_malformed_value(monkeypatch, tmp_path):
    p = _set_path(monkeypatch, tmp_path)
    p.write_text(json.dumps({"news": "not-a-count"}))
    expired = tool_mute.decrement_counts()
    assert expired == 1
    assert tool_mute.is_muted("news") is False


def test_unmute_removes_entry(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=5)
    assert tool_mute.unmute("news") is True
    assert tool_mute.is_muted("news") is False
    assert tool_mute.unmute("news") is False


def test_is_muted_handles_missing_file(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    assert tool_mute.is_muted("news") is False


def test_active_mutes_returns_full_state(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    tool_mute.mute("news", cycles=2)
    tool_mute.mute("docker", cycles=None)
    state = tool_mute.active_mutes()
    assert state == {"news": 2, "docker": None}
