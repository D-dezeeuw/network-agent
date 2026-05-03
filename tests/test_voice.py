import subprocess
from unittest.mock import MagicMock, patch

import pytest

import overrides
import voice


def _set_overrides_path(monkeypatch, tmp_path):
    """Redirect overrides storage to a temp file so /set is sandbox-safe."""
    p = tmp_path / "overrides.json"
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", str(p))
    monkeypatch.setattr(overrides, "STATE_DIR", str(tmp_path))
    return p


# --- _strip_formatting --------------------------------------------------------

def test_strip_formatting_removes_html_tags():
    assert voice._strip_formatting("<b>Healthy</b>") == "Healthy"


def test_strip_formatting_removes_markdown_punctuation():
    assert voice._strip_formatting("**bold** and `code` and # head") == "bold and code and head"


def test_strip_formatting_collapses_whitespace():
    assert voice._strip_formatting("a\n\nb\t  c") == "a b c"


# --- _truncate_at_sentence ----------------------------------------------------

def test_truncate_keeps_short_text_intact():
    assert voice._truncate_at_sentence("hi.", 100) == "hi."


def test_truncate_prefers_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence."
    out = voice._truncate_at_sentence(text, 32)
    assert out.endswith(".")
    assert len(out) <= 32


def test_truncate_falls_back_to_hard_cap_when_no_late_period():
    """If the only period is in the first half, hard-cap rather than waste room."""
    text = "x." + "y" * 100
    out = voice._truncate_at_sentence(text, 50)
    assert len(out) == 50


# --- generate_voice_summary ---------------------------------------------------

def _stub_chat_response(text):
    """Build a minimal object shaped like an OpenAI chat completion."""
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


def test_generate_voice_summary_strips_html_and_markdown(monkeypatch):
    raw = "<b>Status</b>: **healthy** with `12%` CPU"
    with patch.object(voice.client.chat.completions, "create",
                      return_value=_stub_chat_response(raw)):
        out = voice.generate_voice_summary({}, {}, {}, [])
    assert "<" not in out
    assert "*" not in out
    assert "`" not in out


def test_generate_voice_summary_truncates_to_cap(monkeypatch, tmp_path):
    _set_overrides_path(monkeypatch, tmp_path)
    long_text = ("Sentence. " * 1000).strip()  # ~10000 chars
    with patch.object(voice.client.chat.completions, "create",
                      return_value=_stub_chat_response(long_text)):
        out = voice.generate_voice_summary({}, {}, {}, [])
    assert len(out) <= voice.TTS_MAX_CHARS


def test_generate_voice_summary_returns_error_string_on_llm_failure():
    with patch.object(voice.client.chat.completions, "create",
                      side_effect=Exception("openrouter down")):
        out = voice.generate_voice_summary({}, {}, {}, [])
    assert "openrouter down" in out


# --- synthesize_speech --------------------------------------------------------

def test_synthesize_speech_calls_openrouter_with_correct_args():
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"\xff\xfb\x90mp3-bytes"
    with patch.object(voice.client.audio.speech, "create",
                      return_value=fake_resp) as create:
        result = voice.synthesize_speech("hello")
    assert result == b"\xff\xfb\x90mp3-bytes"
    args = create.call_args.kwargs
    assert args["model"] == voice.TTS_MODEL
    assert args["voice"] == "alloy"
    assert args["input"] == "hello"
    assert args["response_format"] == "mp3"
    assert args["speed"] == 1.0


def test_synthesize_speech_uses_overrides(monkeypatch, tmp_path):
    _set_overrides_path(monkeypatch, tmp_path)
    overrides.set_override("TTS_VOICE", "shimmer")
    overrides.set_override("TTS_MODEL", "google/gemini-3.1-flash-tts-preview")
    overrides.set_override("TTS_SPEED", "1.25")
    fake_resp = MagicMock()
    fake_resp.read.return_value = b""
    with patch.object(voice.client.audio.speech, "create",
                      return_value=fake_resp) as create:
        voice.synthesize_speech("x")
    args = create.call_args.kwargs
    assert args["voice"] == "shimmer"
    assert args["model"] == "google/gemini-3.1-flash-tts-preview"
    assert args["speed"] == 1.25


def test_synthesize_speech_falls_back_to_content_attr():
    """Older SDK builds expose .content rather than .read()."""
    fake_resp = MagicMock(spec=["content"])
    fake_resp.content = b"old-sdk-bytes"
    with patch.object(voice.client.audio.speech, "create",
                      return_value=fake_resp):
        result = voice.synthesize_speech("x")
    assert result == b"old-sdk-bytes"


def test_synthesize_speech_wraps_sdk_errors():
    with (
        patch.object(voice.client.audio.speech, "create",
                     side_effect=Exception("rate limit")),
        pytest.raises(RuntimeError, match="rate limit"),
    ):
        voice.synthesize_speech("x")


# --- mp3_to_ogg_opus ----------------------------------------------------------

def test_mp3_to_ogg_opus_invokes_ffmpeg_with_libopus():
    fake_proc = MagicMock(stdout=b"OggS\x00...")
    with patch("voice.subprocess.run", return_value=fake_proc) as run:
        out = voice.mp3_to_ogg_opus(b"mp3-data")
    assert out.startswith(b"OggS")
    cmd = run.call_args.args[0]
    assert "ffmpeg" in cmd
    assert "libopus" in cmd
    assert "pipe:0" in cmd
    assert "pipe:1" in cmd


def test_mp3_to_ogg_opus_raises_when_ffmpeg_missing():
    with (
        patch("voice.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="ffmpeg not installed"),
    ):
        voice.mp3_to_ogg_opus(b"x")


def test_mp3_to_ogg_opus_wraps_ffmpeg_errors():
    err = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"invalid input")
    with (
        patch("voice.subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="invalid input"),
    ):
        voice.mp3_to_ogg_opus(b"x")


def test_mp3_to_ogg_opus_wraps_timeout():
    err = subprocess.TimeoutExpired("ffmpeg", 30)
    with (
        patch("voice.subprocess.run", side_effect=err),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        voice.mp3_to_ogg_opus(b"x")
