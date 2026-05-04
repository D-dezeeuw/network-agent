from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import main
import overrides


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


# --- _digest_mode resolver ---------------------------------------------------

def _set_overrides_path(monkeypatch, tmp_path):
    p = tmp_path / "overrides.json"
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", str(p))
    monkeypatch.setattr(overrides, "STATE_DIR", str(tmp_path))
    return p


def test_digest_mode_defaults_to_text(monkeypatch, tmp_path):
    _set_overrides_path(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "DIGEST_MODE", "text")
    monkeypatch.delenv("DIGEST_MODE", raising=False)
    assert main._digest_mode() == "text"


def test_digest_mode_normalizes_case(monkeypatch, tmp_path):
    _set_overrides_path(monkeypatch, tmp_path)
    overrides.set_override("DIGEST_MODE", "VOICE")
    assert main._digest_mode() == "voice"


def test_digest_mode_override_beats_env(monkeypatch, tmp_path):
    _set_overrides_path(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "DIGEST_MODE", "text")
    overrides.set_override("DIGEST_MODE", "voice")
    assert main._digest_mode() == "voice"


# --- _send_voice_digest ------------------------------------------------------

def test_send_voice_digest_happy_path_calls_send_voice():
    inputs = {"metrics": {}, "health": {}, "security": {}, "news": [], "fail2ban": {}}
    with (
        patch("main.voice.generate_voice_summary", return_value="hello"),
        patch("main.voice.render_audio",
              return_value=(b"OggS-data", "voice", "")),
        patch("main.send_voice", return_value=True) as v,
        patch("main.send_audio") as a,
        patch("main.send_message") as m,
    ):
        main._send_voice_digest(inputs, target_chat_id=42)
    v.assert_called_once_with(b"OggS-data", "", 42)
    a.assert_not_called()
    m.assert_not_called()


def test_send_voice_digest_uses_send_audio_when_method_is_audio():
    inputs = {"metrics": {}, "health": {}, "security": {}, "news": [], "fail2ban": {}}
    with (
        patch("main.voice.generate_voice_summary", return_value="hi"),
        patch("main.voice.render_audio",
              return_value=(b"\xff\xfbmp3-data", "audio", "")),
        patch("main.send_voice") as v,
        patch("main.send_audio", return_value=True) as a,
        patch("main.send_message") as m,
    ):
        main._send_voice_digest(inputs, target_chat_id=42)
    v.assert_not_called()
    a.assert_called_once_with(b"\xff\xfbmp3-data", "", 42)
    m.assert_not_called()


def test_send_voice_digest_falls_back_to_text_on_synth_failure():
    inputs = {"metrics": {}, "health": {}, "security": {}, "news": [], "fail2ban": {}}
    with (
        patch("main.voice.generate_voice_summary", return_value="prose summary"),
        patch("main.voice.render_audio",
              return_value=(None, "", "TTS failed: rate limit")),
        patch("main.send_voice") as v,
        patch("main.send_audio") as a,
        patch("main.send_message", return_value=True) as m,
    ):
        main._send_voice_digest(inputs, target_chat_id=42)
    v.assert_not_called()
    a.assert_not_called()
    # text fallback contains the failure reason AND the prose
    args = m.call_args
    body = args.args[0] if args.args else args.kwargs.get("text", "")
    assert "TTS failed: rate limit" in body
    assert "prose summary" in body
    assert "🔇" in body


def test_send_voice_digest_falls_back_to_text_on_upload_failure():
    inputs = {"metrics": {}, "health": {}, "security": {}, "news": [], "fail2ban": {}}
    with (
        patch("main.voice.generate_voice_summary", return_value="prose"),
        patch("main.voice.render_audio",
              return_value=(b"OggS", "voice", "")),
        patch("main.send_voice", return_value=False),
        patch("main.send_message", return_value=True) as m,
    ):
        main._send_voice_digest(inputs, target_chat_id=42)
    body = m.call_args.args[0]
    assert "upload failed" in body.lower()
    assert "prose" in body
