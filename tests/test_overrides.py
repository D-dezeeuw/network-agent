import json

import overrides


def _set_path(monkeypatch, tmp_path):
    p = tmp_path / "overrides.json"
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", str(p))
    monkeypatch.setattr(overrides, "STATE_DIR", str(tmp_path))
    return p


def test_settable_keys_match_known_set():
    assert "OPENROUTER_MODEL" in overrides.SETTABLE_KEYS
    assert "QUIET_HOURS" in overrides.SETTABLE_KEYS
    assert "REPORT_HOUR" in overrides.SETTABLE_KEYS
    assert "REPORT_INTERVAL_HOURS" in overrides.SETTABLE_KEYS


def test_set_override_persists(monkeypatch, tmp_path):
    p = _set_path(monkeypatch, tmp_path)
    overrides.set_override("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    raw = json.loads(p.read_text())
    assert raw["OPENROUTER_MODEL"] == "openai/gpt-4o-mini"


def test_set_override_coerces_int(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    overrides.set_override("REPORT_HOUR", "9")
    assert overrides.load_overrides()["REPORT_HOUR"] == 9


def test_set_override_rejects_bad_int(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    import pytest
    with pytest.raises(ValueError):
        overrides.set_override("REPORT_HOUR", "not-a-number")


def test_set_override_rejects_unsettable_key(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    import pytest
    with pytest.raises(ValueError):
        overrides.set_override("TELEGRAM_BOT_TOKEN", "leaked")


def test_unset_override_returns_true_when_present(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    overrides.set_override("OPENROUTER_MODEL", "x/y")
    assert overrides.unset_override("OPENROUTER_MODEL") is True
    assert overrides.unset_override("OPENROUTER_MODEL") is False


def test_effective_prefers_override_over_env(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "from-env")
    overrides.set_override("OPENROUTER_MODEL", "from-override")
    assert overrides.effective("OPENROUTER_MODEL") == "from-override"


def test_effective_falls_back_to_env(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "from-env")
    assert overrides.effective("OPENROUTER_MODEL") == "from-env"


def test_effective_falls_back_to_default(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert overrides.effective("OPENROUTER_MODEL", "fallback") == "fallback"


def test_effective_int_returns_int(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    overrides.set_override("REPORT_HOUR", "12")
    assert overrides.effective_int("REPORT_HOUR") == 12


def test_effective_int_returns_default_on_missing(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    monkeypatch.delenv("REPORT_HOUR", raising=False)
    assert overrides.effective_int("REPORT_HOUR", default=8) == 8


def test_corrupt_overrides_file_returns_empty(monkeypatch, tmp_path):
    p = _set_path(monkeypatch, tmp_path)
    p.write_text("{not valid")
    assert overrides.load_overrides() == {}


def test_report_config_marks_source(monkeypatch, tmp_path):
    _set_path(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "from-env")
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    overrides.set_override("REPORT_HOUR", "7")
    rows = {r["key"]: r for r in overrides.report_config()}
    assert rows["OPENROUTER_MODEL"]["source"] == "env"
    assert rows["OPENROUTER_MODEL"]["value"] == "from-env"
    assert rows["REPORT_HOUR"]["source"] == "override"
    assert rows["REPORT_HOUR"]["value"] == "7"
    assert rows["QUIET_HOURS"]["source"] == "default"
