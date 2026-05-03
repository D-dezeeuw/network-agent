"""Voice summary generation and TTS synthesis.

Produces a short, voice-friendly summary of current server state and
synthesizes it to speech via OpenRouter's TTS endpoint (OpenAI-SDK
compatible). Reuses the existing OpenRouter client from `ai.py` — no
separate API key.
"""

import re
import subprocess

from ai import _model, client
from config import TTS_MAX_CHARS, TTS_MODEL, TTS_SPEED, TTS_VOICE
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


def synthesize_speech(text: str, voice: str | None = None,
                      model: str | None = None) -> bytes:
    """Synthesize `text` to MP3 bytes via OpenRouter's TTS endpoint.

    Raises RuntimeError on any synthesis failure so the caller has a
    single exception class to catch and fall back from.
    """
    voice = voice or effective("TTS_VOICE", TTS_VOICE) or "alloy"
    model = model or effective("TTS_MODEL", TTS_MODEL) or TTS_MODEL
    try:
        speed = float(effective("TTS_SPEED", str(TTS_SPEED)) or "1.0")
    except (TypeError, ValueError):
        speed = 1.0
    try:
        resp = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format="mp3",
            speed=speed,
        )
        # SDK exposes .read() (preferred) and .content. Try .read() first
        # for forward-compat; fall back to .content for older builds.
        if hasattr(resp, "read"):
            return resp.read()
        return resp.content
    except Exception as e:
        raise RuntimeError(f"TTS synthesis failed: {e}") from e


def mp3_to_ogg_opus(mp3_bytes: bytes) -> bytes:
    """Transcode MP3 → OGG-Opus via ffmpeg pipe.

    Required for Telegram's voice-message bubble (sendVoice expects
    OGG-Opus, and OpenRouter's TTS only emits MP3 or PCM). 32kbps
    Opus matches Telegram's own voice-message bitrate.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "error",
             "-i", "pipe:0",
             "-c:a", "libopus", "-b:a", "32k", "-ar", "48000",
             "-f", "ogg", "pipe:1"],
            input=mp3_bytes,
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
