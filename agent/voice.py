"""Voice summary generation and TTS synthesis.

Produces a short, voice-friendly summary of current server state and
synthesizes it to speech via OpenRouter's TTS endpoint (OpenAI-SDK
compatible). Reuses the existing OpenRouter client from `ai.py` — no
separate API key.
"""

import re
import subprocess

from ai import _model, client
from config import (
    TTS_MAX_CHARS,
    TTS_MODEL,
    TTS_PCM_SAMPLE_RATE,
    TTS_RESPONSE_FORMAT,
    TTS_SPEED,
    TTS_VOICE,
)
from overrides import effective, effective_int


def _strip_formatting(text: str) -> str:
    """Strip residual HTML and Markdown punctuation so the TTS engine
    doesn't read tag names or asterisks aloud."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_at_sentence(text: str, cap: int) -> str:
    """Trim to <= cap chars, preferring the last sentence boundary."""
    if len(text) <= cap:
        return text
    head = text[:cap]
    last_period = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
    if last_period >= cap // 2:
        return head[: last_period + 1]
    return head


def generate_voice_summary(
    metrics: dict,
    health: dict,
    security: dict,
    news: list,
    trends: dict | None = None,
    fail2ban: dict | None = None,
) -> str:
    """Ask the LLM for a 30–60s spoken summary as plain prose."""
    trends_block = trends or {"deltas": {}, "disk_forecasts": {}}
    fail2ban_block = fail2ban or {"enabled": False}
    prompt = f"""\
You are an ops assistant briefing a server admin out loud. Produce a
SPOKEN summary of the server's current state — plain prose only, no
markdown, no HTML tags, no emoji, no bullet points, no headings.

Length: 30–60 seconds when read aloud (about 75–150 words). Hard cap
{TTS_MAX_CHARS} characters.

Lead with the overall verdict ("healthy", "warning", or "critical").
Then mention at most two noteworthy findings and at most two relevant
CVEs. Skip everything else — this is a top-line summary, not a digest.

Use natural sentences. Read numbers as words where it sounds better
("twelve percent" over "12%"). Don't list — narrate.

If the data shows nothing of note, say so in one short sentence and stop.

Data:

## Host Security Scan
{security}

## System Health
{health}

## Server Metrics
{metrics}

## Trends
{trends_block}

## Fail2ban
{fail2ban_block}

## Security News
{news}
"""
    try:
        response = client.chat.completions.create(
            model=_model(),
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
    except Exception as e:
        print(f"[voice] LLM error: {e}")
        return f"Voice summary unavailable: {e}"

    cleaned = _strip_formatting(content)
    cap = effective_int("TTS_MAX_CHARS", TTS_MAX_CHARS) or TTS_MAX_CHARS
    return _truncate_at_sentence(cleaned, cap)


def _resolve_response_format() -> str:
    """Effective TTS_RESPONSE_FORMAT, normalized + validated."""
    fmt = (effective("TTS_RESPONSE_FORMAT", TTS_RESPONSE_FORMAT) or "mp3").lower()
    return fmt if fmt in ("mp3", "pcm") else "mp3"


def _resolve_pcm_sample_rate() -> int:
    return effective_int("TTS_PCM_SAMPLE_RATE", TTS_PCM_SAMPLE_RATE) or 24000


def synthesize_speech(text: str, voice: str | None = None,
                      model: str | None = None,
                      response_format: str | None = None) -> bytes:
    """Synthesize `text` to audio bytes via OpenRouter's TTS endpoint.

    Returns raw MP3 or PCM bytes depending on `TTS_RESPONSE_FORMAT`
    (mp3 by default; pcm for providers like Gemini that don't emit mp3).
    Raises RuntimeError on any synthesis failure so the caller has a
    single exception class to catch and fall back from.
    """
    voice = voice or effective("TTS_VOICE", TTS_VOICE) or "alloy"
    model = model or effective("TTS_MODEL", TTS_MODEL) or TTS_MODEL
    response_format = (response_format or _resolve_response_format()).lower()
    try:
        speed = float(effective("TTS_SPEED", str(TTS_SPEED)) or "1.0")
    except (TypeError, ValueError):
        speed = 1.0
    try:
        resp = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format=response_format,
            speed=speed,
        )
        # SDK exposes .read() (preferred) and .content. Try .read() first
        # for forward-compat; fall back to .content for older builds.
        if hasattr(resp, "read"):
            return resp.read()
        return resp.content
    except Exception as e:
        raise RuntimeError(f"TTS synthesis failed: {e}") from e


def _ffmpeg_input_args(source_format: str, sample_rate: int) -> list[str]:
    """Input flags telling ffmpeg how to read the upstream bytes.

    For raw PCM there's no container — we have to declare the codec,
    sample rate, and channel layout ourselves. OpenRouter's PCM is
    documented as signed 16-bit little-endian mono.
    """
    if source_format == "pcm":
        return ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
                "-i", "pipe:0"]
    return ["-i", "pipe:0"]


def _run_ffmpeg(args: list[str], audio_bytes: bytes) -> bytes:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error", *args],
            input=audio_bytes,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return proc.stdout
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg not installed") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"transcode failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("transcode timed out") from e


def to_ogg_opus(audio_bytes: bytes, source_format: str = "mp3",
                sample_rate: int | None = None) -> bytes:
    """Transcode upstream TTS audio (MP3 or raw PCM) → OGG-Opus.

    Required for Telegram's voice-message bubble (sendVoice expects
    OGG-Opus). 32kbps Opus matches Telegram's own voice bitrate.
    `sample_rate` only matters for `source_format="pcm"` — it must
    match the rate the TTS provider emitted at.
    """
    rate = sample_rate or _resolve_pcm_sample_rate()
    args = [
        *_ffmpeg_input_args(source_format, rate),
        "-c:a", "libopus", "-b:a", "32k", "-ar", "48000",
        "-f", "ogg", "pipe:1",
    ]
    return _run_ffmpeg(args, audio_bytes)


def pcm_to_mp3(pcm_bytes: bytes, sample_rate: int | None = None) -> bytes:
    """Wrap raw PCM in an MP3 container so Telegram sendAudio can play it.

    Used when TTS_RESPONSE_FORMAT=pcm and TTS_AS_VOICE_MESSAGE=false
    (audio-attachment mode rather than voice bubble).
    """
    rate = sample_rate or _resolve_pcm_sample_rate()
    args = [
        *_ffmpeg_input_args("pcm", rate),
        "-c:a", "libmp3lame", "-b:a", "64k",
        "-f", "mp3", "pipe:1",
    ]
    return _run_ffmpeg(args, pcm_bytes)


def render_audio(text: str) -> tuple[bytes | None, str, str]:
    """Run text → ready-to-upload audio bytes, picking the right method.

    Returns (audio_bytes, method, error). On any failure returns
    (None, "", error_string) — caller is responsible for posting a
    text fallback. method is either "voice" (sendVoice with audio/ogg)
    or "audio" (sendAudio with audio/mpeg) depending on
    TTS_AS_VOICE_MESSAGE plus the upstream response_format.
    """
    try:
        raw = synthesize_speech(text)
    except RuntimeError as e:
        return None, "", f"TTS failed: {e}"

    as_voice = (effective("TTS_AS_VOICE_MESSAGE", "true") or "true").lower() == "true"
    fmt = _resolve_response_format()

    if as_voice:
        try:
            ogg = to_ogg_opus(raw, fmt)
        except RuntimeError as e:
            return None, "", f"Transcode failed: {e}"
        return ogg, "voice", ""

    # audio-attachment mode: PCM upstream still needs MP3 wrapping; MP3
    # upstream passes through as-is.
    if fmt == "pcm":
        try:
            audio = pcm_to_mp3(raw)
        except RuntimeError as e:
            return None, "", f"Transcode failed: {e}"
        return audio, "audio", ""
    return raw, "audio", ""
