# TTS Voice Summary — Implementation Plan

Standalone follow-up to [implementation-plan.md](implementation-plan.md). Adds a `/speak` slash command that generates a short, voice-friendly summary of the current server state and uploads it to Telegram as a voice message.

Single feature, single branch, single PR — not phased.

## Branch

`feat/voice-summary`

## Goal

`/speak` produces a 30–60s spoken summary of the current server state (overall verdict + 1–2 noteworthy findings + 1–2 relevant CVEs), uploads it as a Telegram voice (or audio) message in the calling chat, and follows the same authorization rules as the other slash commands.

## Provider choice — OpenRouter

**OpenRouter TTS** via `POST /api/v1/audio/speech` — OpenAI-SDK compatible, accessed by setting `base_url=OPENROUTER_BASE_URL` on the existing `openai` client. **Reuses the existing `OPENROUTER_API_KEY` — no second API key.**

- Same SDK already pinned in `requirements.txt` (`openai>=1.40.0`)
- Same client object that [agent/ai.py:9](agent/ai.py#L9) already uses for chat completions — `client.audio.speech.create(...)` works against the same instance
- Single billing surface — usage shows up alongside the existing chat-completion spend in OpenRouter
- Model and voice are runtime-configurable, so we can swap providers without code changes

**Caveats from OpenRouter's TTS API:**

- `response_format` only supports `mp3` or `pcm` — **not `opus`**. Telegram's voice-message bubble (round avatar + waveform) requires OGG-Opus, so we transcode MP3 → OGG-Opus with `ffmpeg` before calling `sendVoice`. A `TTS_AS_VOICE_MESSAGE=false` escape hatch skips transcoding and uses `sendAudio` (regular audio attachment, no ffmpeg required).
- Some models (e.g. `gemini-3.1-flash-tts-preview`) may have a different voice catalog — voice strings are validated by the provider, not by us. Bad voice → synthesis error → text fallback.

### Default model

`openai/gpt-4o-mini-tts-2025-12-15` — ~$0.60/M chars (~$0.0005 per `/speak`), OpenAI voice catalog (`alloy`, `nova`, `shimmer`, etc).

Other models surfaced via env/override:

- `mistralai/voxtral-mini-tts-2603` — pricier (~$16/M), supports zero-shot voice cloning
- `google/gemini-3.1-flash-tts-preview` — multilingual (70+ languages), preview pricing

## Architectural fit

The current code uses two parallel client patterns we have to respect:

- [agent/ai.py:9](agent/ai.py#L9) instantiates `OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)`. **We reuse this same client** — no new module-level client. The OpenAI SDK supports `client.audio.speech.create(...)` against any base URL that exposes the OpenAI Audio Speech endpoint, and OpenRouter does.
- [agent/tg_publish.py](agent/tg_publish.py) uses raw `requests` (not the PTB client) for digest-channel sends. The new `send_voice` / `send_audio` helpers follow that same pattern so they're symmetric with `send_photo` and reusable from non-bot contexts.
- The bot at [agent/bot.py:883](agent/bot.py#L883) (`cmd_preview`) is the closest behavioral analog: read-only, chat-scoped, no snapshot writes. `/speak` mirrors that plumbing but generates audio instead of text.
- [agent/overrides.py:30](agent/overrides.py#L30) gates runtime-settable keys via `SETTABLE_KEYS` — the new `TTS_*` keys must be added there explicitly or `/set TTS_VOICE shimmer` will reject.

## Tasks

### 1. Configuration — env vars and overrides

All new variables are TTS-scoped and prefixed `TTS_` so they group cleanly in `/config`.

- [ ] **Add to [`agent/config.py`](agent/config.py)** (module-level, near the other env reads):

  ```python
  TTS_MODEL = os.getenv("TTS_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")
  TTS_VOICE = os.getenv("TTS_VOICE", "alloy")
  TTS_AS_VOICE_MESSAGE = os.getenv("TTS_AS_VOICE_MESSAGE", "true").lower() == "true"
  TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "3000"))
  TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
  ```

- [ ] **Add to `SETTABLE_KEYS` in [agent/overrides.py:30](agent/overrides.py#L30)**:

  ```python
  "TTS_MODEL": _coerce_str,
  "TTS_VOICE": _coerce_str,
  "TTS_AS_VOICE_MESSAGE": _coerce_str,   # parsed back to bool by callers via .lower() == "true"
  "TTS_MAX_CHARS": _coerce_int,
  "TTS_SPEED": _coerce_str,              # float coerced at use-site to allow "1.0"
  ```

  No `OPENROUTER_API_KEY` exposure — secrets stay env-only, matching the existing whitelist policy.

- [ ] **Update the `/set` usage hint** in [agent/bot.py:736](agent/bot.py#L736) to mention the new keys.

- [ ] **Expand [`.env.example`](.env.example)** with a clearly grouped TTS block (full text under §6 below).

- [ ] **Expand [`docker-compose.yml`](docker-compose.yml)** — pass all five TTS vars through:

  ```yaml
  TTS_MODEL: ${TTS_MODEL:-openai/gpt-4o-mini-tts-2025-12-15}
  TTS_VOICE: ${TTS_VOICE:-alloy}
  TTS_AS_VOICE_MESSAGE: ${TTS_AS_VOICE_MESSAGE:-true}
  TTS_MAX_CHARS: ${TTS_MAX_CHARS:-3000}
  TTS_SPEED: ${TTS_SPEED:-1.0}
  ```

- [ ] **Add `ffmpeg` to [`Dockerfile`](Dockerfile)** — append to the existing `apt-get install` line. Required only when `TTS_AS_VOICE_MESSAGE=true`; harmless if unused.

  ```dockerfile
  RUN apt-get update && apt-get install -y \
      systemd \
      ffmpeg \
      && rm -rf /var/lib/apt/lists/*
  ```

  ffmpeg adds ~50–80MB to the image — acceptable for this single-host operational tool.

### 2. New module `agent/voice.py`

Three narrowly scoped functions. No module-level client — we import the existing `client` from `ai.py` so there's a single source of truth for OpenRouter credentials.

- [ ] **Imports**:

  ```python
  import re
  import subprocess
  from ai import client          # reuses OPENROUTER_API_KEY + OPENROUTER_BASE_URL
  from config import TTS_MODEL, TTS_VOICE, TTS_MAX_CHARS, TTS_SPEED
  from overrides import effective, effective_int
  ```

- [ ] **`generate_voice_summary(metrics, health, security, news, trends, fail2ban) -> str`**

  Same input contract as `generate_report` in [agent/ai.py:54](agent/ai.py#L54) so the caller can reuse the same data-collection step.

  Calls the OpenRouter chat client (already wired through overrides via [agent/ai.py:13](agent/ai.py#L13)'s `_model()` helper — reuse that, don't re-implement) with a TTS-tuned prompt:

  - "Speak in plain prose, no markdown, no HTML, no emoji, no bullet points."
  - "30–60 seconds when read aloud (≈75–150 words)."
  - "Lead with overall verdict (healthy / warning / critical). Then mention at most two noteworthy findings and at most two relevant CVEs. Skip the rest."
  - "Use natural sentences. Read numbers as words where it sounds better ('twelve percent' over '12%')."

  Strip residual HTML/markdown defensively before returning:

  ```python
  text = re.sub(r"<[^>]+>", "", text)              # HTML tags
  text = re.sub(r"[*_`#]+", "", text)               # markdown punctuation
  text = re.sub(r"\s+", " ", text).strip()
  ```

  Truncate to `effective_int("TTS_MAX_CHARS", TTS_MAX_CHARS)` at the last sentence boundary as the final guard.

- [ ] **`synthesize_speech(text: str, voice: str | None = None, model: str | None = None) -> bytes`**

  - `voice = voice or effective("TTS_VOICE", TTS_VOICE) or "alloy"`
  - `model = model or effective("TTS_MODEL", TTS_MODEL) or "openai/gpt-4o-mini-tts-2025-12-15"`
  - `speed = float(effective("TTS_SPEED", str(TTS_SPEED)) or "1.0")`
  - Call: `resp = client.audio.speech.create(model=model, voice=voice, input=text, response_format="mp3", speed=speed)`
  - Return `resp.read()` (preferred) or `resp.content` (fallback for older SDK builds).
  - Wrap in try/except → re-raise as `RuntimeError(f"TTS synthesis failed: {e}")` so `cmd_speak` has a single exception class to catch.

- [ ] **`mp3_to_ogg_opus(mp3_bytes: bytes) -> bytes`**

  Subprocess call — pipe MP3 in via stdin, OGG-Opus out via stdout:

  ```python
  proc = subprocess.run(
      ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
       "-c:a", "libopus", "-b:a", "32k", "-ar", "48000",
       "-f", "ogg", "pipe:1"],
      input=mp3_bytes, capture_output=True, check=True, timeout=30,
  )
  return proc.stdout
  ```

  Raises `RuntimeError("ffmpeg not installed")` on `FileNotFoundError`. Other `CalledProcessError` → `RuntimeError(f"transcode failed: {stderr}")`.

  32 kbps Opus is plenty for speech — Telegram's own voice messages are around that bitrate.

### 3. New helpers in `tg_publish.py`

Insert both after `send_photo` in [agent/tg_publish.py](agent/tg_publish.py).

- [ ] **`send_voice(audio_ogg: bytes, caption: str = "", chat_id: ChatId = None) -> bool`**

  Multipart POST to `https://api.telegram.org/bot{token}/sendVoice`, `files={"voice": ("summary.ogg", audio_ogg, "audio/ogg")}`, form fields `chat_id` and (if non-empty) `caption` truncated to 1024 chars with `parse_mode=HTML`.

  **Critical detail**: MIME type **must** be `audio/ogg` — `audio/opus` will render as a generic file attachment. This is the single most failable detail in the whole feature; verify it visually in Telegram.

- [ ] **`send_audio(audio_mp3: bytes, caption: str = "", chat_id: ChatId = None) -> bool`**

  Same shape but POSTs to `/sendAudio`, `files={"audio": ("summary.mp3", audio_mp3, "audio/mpeg")}`.

Both return `False` on `RequestException`, never raise. Logs via `print(...)` to match the existing module style.

### 4. New `/speak` command in `agent/bot.py`

Modeled on `cmd_preview` ([agent/bot.py:883](agent/bot.py#L883)) but with audio output and explicit text fallbacks.

- [ ] **Authorization gate**: `_is_authorized_update(update)` → `_refuse(update)` on fail (identical to every other handler).

- [ ] **Data collection**: factor the muted-source-aware collection block out of [agent/main.py:60-80](agent/main.py#L60-L80) into a `collect_inputs() -> dict` helper in `main.py` and call it from both `run_agent` and `cmd_speak`. Otherwise two copies of the muted-source short-circuit logic will drift.

- [ ] **Pipeline**:

  1. `await chat.send_message("Generating voice summary…")` so the user sees activity.
  2. `summary = await asyncio.to_thread(generate_voice_summary, ...)` — Claude call, blocking I/O.
  3. `mp3 = await asyncio.to_thread(synthesize_speech, summary)` — TTS call, blocking I/O.
  4. Branch on `effective("TTS_AS_VOICE_MESSAGE", "true").lower() == "true"`:
     - **voice mode**: `ogg = await asyncio.to_thread(mp3_to_ogg_opus, mp3)`, then `await asyncio.to_thread(send_voice, ogg, "", chat.id)`.
     - **audio mode**: `await asyncio.to_thread(send_audio, mp3, "", chat.id)` — skip transcoding.
  5. If the upload returned `False`, fall through to text fallback.

- [ ] **Fallback shape — explicit, never silent**. The same `summary` text is always available, so the user never loses information when audio fails.

  | Failure | Prefix sent with text |
  | --- | --- |
  | `synthesize_speech` raises | `🔇 TTS failed (<error>) — text only:` |
  | `mp3_to_ogg_opus` raises (e.g. ffmpeg missing) | `🔇 Transcode failed (<error>) — text only:` |
  | `send_voice` / `send_audio` returns `False` | `🔇 Upload failed — text only:` |

- [ ] **No state writes**: do not call `save_snapshot`, `decrement_tool_mutes`, or `_archive_report`. `/speak` is read-only, like `/preview`.

- [ ] **No mute / quiet-hours check**: explicit user intent overrides those gates, matching `/runnow`.

### 5. Wire up in the command menu

- [ ] `app.add_handler(CommandHandler("speak", cmd_speak))` in `build_application()` at [agent/bot.py:1025](agent/bot.py#L1025).
- [ ] `BotCommand("speak", "Voice summary of current state")` in `BOT_COMMAND_MENU` at [agent/bot.py:86](agent/bot.py#L86).
- [ ] Add `<b>/speak</b> — voice summary of current state\n` to `HELP_TEXT` at [agent/bot.py:54](agent/bot.py#L54), placed near `/preview`.

### 6. Expanded `.env.example` block

Append this section at the bottom of [`.env.example`](.env.example), preserving the existing structure:

```env
# ──────────────────────────────────────────────────────────────────────
# Voice summary (/speak)
# ──────────────────────────────────────────────────────────────────────
# Routes through the same OpenRouter API key as the chat completions —
# no separate provider account needed.
#
# Models (set TTS_MODEL):
#   openai/gpt-4o-mini-tts-2025-12-15   (~$0.60/M chars, OpenAI voices) ← default
#   mistralai/voxtral-mini-tts-2603     (~$16/M chars, voice cloning)
#   google/gemini-3.1-flash-tts-preview (multilingual, 70+ languages)
TTS_MODEL=openai/gpt-4o-mini-tts-2025-12-15

# Voice name. Catalog depends on TTS_MODEL — for OpenAI's TTS:
# alloy, nova, shimmer, echo, fable, onyx (check OpenRouter model page
# for other providers' voice IDs).
TTS_VOICE=alloy

# When true, transcode MP3 → OGG-Opus via ffmpeg and send as a Telegram
# voice message (round avatar + waveform UI). Requires ffmpeg in image.
# When false, send the raw MP3 as a regular audio attachment — no
# ffmpeg dependency, but a less polished UI.
TTS_AS_VOICE_MESSAGE=true

# Cost guardrail — characters of input text passed to the TTS endpoint.
# Output text is truncated at the last sentence boundary below this cap.
TTS_MAX_CHARS=3000

# Playback speed multiplier. 1.0 = normal. Some models ignore this.
TTS_SPEED=1.0
```

### 7. Tests — new file `tests/test_voice.py`

Use the existing pattern from [tests/test_bot.py](tests/test_bot.py) and [tests/test_tg_publish.py](tests/test_tg_publish.py): unittest.mock for SDK / requests / subprocess, no live network calls.

- [ ] **`test_generate_voice_summary_strips_html_and_markdown`**: stub the chat completion to return a string containing `<b>` tags, `**bold**` markdown, and inline backticks. Assert returned string contains no `<`, no `*`, no backtick.
- [ ] **`test_generate_voice_summary_truncates_to_cap`**: stub the model to return 5000 chars. Assert returned length ≤ `TTS_MAX_CHARS` and ends at a sentence boundary.
- [ ] **`test_synthesize_speech_calls_openrouter_with_correct_args`**: patch `voice.client.audio.speech.create` to a `MagicMock`. Call `synthesize_speech("hi")`. Assert it was called with `model="openai/gpt-4o-mini-tts-2025-12-15"`, `voice="alloy"`, `input="hi"`, `response_format="mp3"`, `speed=1.0`.
- [ ] **`test_synthesize_speech_uses_overrides`**: write `TTS_MODEL` and `TTS_VOICE` overrides via `set_override`, call `synthesize_speech("hi")`, assert the call args reflect the overrides.
- [ ] **`test_synthesize_speech_wraps_sdk_errors`**: patch `client.audio.speech.create` to raise `Exception("rate limit")`. Assert `RuntimeError` is raised with `"rate limit"` in the message.
- [ ] **`test_mp3_to_ogg_opus_invokes_ffmpeg`**: patch `subprocess.run` to return a mock with `stdout=b"OggS..."`. Assert called with `libopus`, `pipe:0`, `pipe:1`. Assert returned bytes start with `OggS`.
- [ ] **`test_mp3_to_ogg_opus_missing_ffmpeg`**: patch `subprocess.run` to raise `FileNotFoundError`. Assert `RuntimeError("ffmpeg not installed")`.
- [ ] **`test_send_voice_uses_audio_ogg_mime`**: patch `requests.post`. Assert URL ends `/sendVoice`, `files["voice"][2] == "audio/ogg"`.
- [ ] **`test_send_audio_uses_audio_mpeg_mime`**: patch `requests.post`. Assert URL ends `/sendAudio`, `files["audio"][2] == "audio/mpeg"`.
- [ ] **`test_send_voice_caption_truncated_to_1024`**: pass a 2000-char caption. Assert the form field is exactly 1024.
- [ ] **`test_cmd_speak_voice_mode_happy_path`**: mock `collect_inputs`, `generate_voice_summary`, `synthesize_speech`, `mp3_to_ogg_opus`, `send_voice`. Assert `send_voice` was called with the OGG bytes and the caller's chat_id.
- [ ] **`test_cmd_speak_audio_mode_skips_transcode`**: same with `TTS_AS_VOICE_MESSAGE=false`. Assert `mp3_to_ogg_opus` was **not** called and `send_audio` was called with MP3 bytes.
- [ ] **`test_cmd_speak_text_fallback_on_synthesis_error`**: `synthesize_speech` raises `RuntimeError("rate limit")`. Assert `chat.send_message` was called with text containing `🔇 TTS failed` and `rate limit`.
- [ ] **`test_cmd_speak_text_fallback_on_transcode_error`**: `mp3_to_ogg_opus` raises `RuntimeError("ffmpeg not installed")`. Assert fallback prefix `🔇 Transcode failed`.
- [ ] **`test_cmd_speak_text_fallback_on_upload_error`**: `send_voice` returns `False`. Assert fallback prefix `🔇 Upload failed`.

### 8. Documentation

- [ ] [`.env.example`](.env.example) updated per §6 above.
- [ ] No README changes — this project doesn't have one. `HELP_TEXT` is the user-facing surface.

## Acceptance

Typing `/speak` in a 1:1 DM with the bot (or the digest channel) produces a voice/audio message that:

- Plays in ~30–60s
- With `TTS_AS_VOICE_MESSAGE=true`: renders as Telegram's voice-message UI (round avatar + waveform)
- With `TTS_AS_VOICE_MESSAGE=false`: renders as a standard audio attachment with a mini player
- Conveys a coherent server-state summary including any current critical findings

**Manual smoke test before merging:**

1. Set `OPENROUTER_API_KEY` (already required for digests), restart container.
2. `/speak` from authorized DM → expect voice bubble within ~5s.
3. `/set TTS_VOICE shimmer`, `/speak` → expect voice in shimmer voice.
4. `/set TTS_VOICE bogus`, `/speak` → expect text fallback with `🔇 TTS failed` prefix.
5. `/set TTS_MODEL google/gemini-3.1-flash-tts-preview`, `/speak` → expect voice from a different provider (and a different voice catalog — bogus voice → fallback).
6. `/set TTS_AS_VOICE_MESSAGE false`, `/speak` → expect audio attachment with mini player, no round avatar.
7. `/unset TTS_VOICE` etc., confirm `/config` no longer lists the override.
8. With ffmpeg deliberately absent (test container without the apt install), `/speak` in voice mode → expect `🔇 Transcode failed (ffmpeg not installed)` fallback.

## Behavior decisions

| Decision | Choice |
| --- | --- |
| Read full digest aloud? | No — too long, poor UX. Generate a separate spoken summary. |
| Where do replies go? | Caller's chat (`update.effective_chat`), not the configured digest channel. |
| Persistence side effects? | None. Read-only run, like `/preview`. |
| Mute / quiet hours respected? | No — `/speak` is explicit user intent (same model as `/runnow`). |
| HTML/Markdown stripping? | Done before TTS; the prompt also asks for plain spoken prose. |
| TTS provider | OpenRouter `/audio/speech` — reuses existing `OPENROUTER_API_KEY` and the SDK client in `ai.py`. |
| Model / voice selection | Env + override keys: `TTS_MODEL`, `TTS_VOICE`. Per-call selection skipped. |
| Voice-message UI vs attachment | Default voice (with ffmpeg transcode); `TTS_AS_VOICE_MESSAGE=false` switches to plain audio with no transcode. |
| TTS failure handling | Reply with the text summary + a short prefixed error note. No silent failure. |
| Cost guardrail | `TTS_MAX_CHARS` cap. No daily quota — single-user agent. |
| Reuse `run_agent` data collection? | Factor the muted-source-aware collection block out of `run_agent` into a `collect_inputs()` helper so both paths share it. |

## Sequencing / commits

Suggested commit breakdown — small, reviewable, each one green on its own:

1. `chore(docker): add ffmpeg to image for TTS transcode`
2. `feat(config): add TTS_* env vars and override keys`
3. `feat(tg_publish): add send_voice and send_audio multipart helpers`
4. `feat(voice): generate_voice_summary, synthesize_speech, mp3_to_ogg_opus`
5. `refactor(main): extract collect_inputs() from run_agent`
6. `feat(bot): /speak command with text fallbacks`
7. `test(voice): unit coverage across happy paths and three fallback branches`

(Optional: collapse the last three into one if review prefers fewer commits.)

## Risks & mitigations

- **Wrong MIME → generic file attachment, not voice UI.** Mitigation: explicit acceptance step with the `audio/ogg` MIME and visual verification in Telegram client.
- **OpenRouter TTS provider rate-limits or rotates models.** Mitigation: model is configurable at runtime, so a swap is a `/set TTS_MODEL ...` away. No restart needed.
- **`generate_voice_summary` hallucinating HTML/markdown despite the prompt.** Mitigation: defensive regex strip after the model call. The prompt is best-effort; the strip is the guarantee.
- **ffmpeg subprocess hanging or producing huge output.** Mitigation: `timeout=30` on the subprocess call, 32kbps fixed bitrate caps output size.
- **Bad voice strings silently producing weird audio.** Mitigation: provider rejects unknown voices → SDK error → text fallback path.
- **Long generation latency (LLM + TTS sequential ≈ 5–10s).** Mitigation: send `"Generating voice summary…"` placeholder so the user sees activity. No streaming in this iteration.

## Out of scope (deliberately)

- Voice replies for Q&A answers (could be a follow-up; not in this iteration)
- Speech-to-text input — Whisper integration is a different track
- Voice cloning or custom-trained voices (Voxtral supports it but we don't expose the upload pathway)
- Streaming TTS (chunk-by-chunk) — single shot is fine for ≤60s payloads
- Multilingual auto-detection — relies on whatever language the model summarizes in
- Caching voice output across rapid `/speak` invocations
