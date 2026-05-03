from unittest.mock import MagicMock, patch

import tg_publish
from tg_publish import html_escape


def test_html_escape_ampersand():
    assert html_escape("a & b") == "a &amp; b"


def test_html_escape_lt_gt():
    assert html_escape("a < b > c") == "a &lt; b &gt; c"


def test_html_escape_combined():
    assert html_escape("<script>x & y</script>") == "&lt;script&gt;x &amp; y&lt;/script&gt;"


def test_html_escape_idempotent_safe_input():
    """Safe input passes through unchanged."""
    assert html_escape("hello world") == "hello world"


def test_html_escape_handles_empty_string():
    assert html_escape("") == ""


def test_html_escape_amp_first_to_avoid_double_escape():
    """If we replaced < before &, &lt; would become &amp;lt;. Order matters."""
    assert html_escape("&<") == "&amp;&lt;"


# --- send_voice / send_audio --------------------------------------------------

def _ok_response():
    r = MagicMock()
    r.raise_for_status.return_value = None
    return r


def test_send_voice_uses_audio_ogg_mime(monkeypatch):
    """Telegram needs `audio/ogg`, not `audio/opus`, for the round-avatar UI."""
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    with patch("tg_publish.requests.post", return_value=_ok_response()) as post:
        ok = tg_publish.send_voice(b"OggS\x00", chat_id=42)
    assert ok is True
    url = post.call_args.args[0]
    assert url.endswith("/sendVoice")
    files = post.call_args.kwargs["files"]
    assert files["voice"][0] == "summary.ogg"
    assert files["voice"][2] == "audio/ogg"
    assert post.call_args.kwargs["data"]["chat_id"] == 42


def test_send_voice_caption_truncated_to_1024(monkeypatch):
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    with patch("tg_publish.requests.post", return_value=_ok_response()) as post:
        tg_publish.send_voice(b"OggS\x00", caption="x" * 2000, chat_id=42)
    assert len(post.call_args.kwargs["data"]["caption"]) == 1024


def test_send_voice_returns_false_on_request_failure(monkeypatch):
    import requests
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    with patch("tg_publish.requests.post",
               side_effect=requests.RequestException("network down")):
        ok = tg_publish.send_voice(b"OggS\x00", chat_id=42)
    assert ok is False


def test_send_voice_returns_false_when_no_chat_target(monkeypatch):
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setattr(tg_publish, "TELEGRAM_CHAT_ID", None)
    assert tg_publish.send_voice(b"OggS\x00") is False


def test_send_voice_short_circuits_on_empty_payload():
    """Empty bytes is treated as a no-op success — same convention as send_photo."""
    assert tg_publish.send_voice(b"") is True


def test_send_audio_uses_audio_mpeg_mime(monkeypatch):
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    with patch("tg_publish.requests.post", return_value=_ok_response()) as post:
        ok = tg_publish.send_audio(b"\xff\xfb", chat_id=42)
    assert ok is True
    url = post.call_args.args[0]
    assert url.endswith("/sendAudio")
    files = post.call_args.kwargs["files"]
    assert files["audio"][0] == "summary.mp3"
    assert files["audio"][2] == "audio/mpeg"


def test_send_audio_returns_false_on_request_failure(monkeypatch):
    import requests
    monkeypatch.setattr(tg_publish, "TELEGRAM_BOT_TOKEN", "T")
    with patch("tg_publish.requests.post",
               side_effect=requests.RequestException("boom")):
        assert tg_publish.send_audio(b"\xff\xfb", chat_id=42) is False
