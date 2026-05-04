"""Runtime config overrides via `/set <KEY> <VALUE>`.

Overrides persist at /state/overrides.json and beat the corresponding
env var. Whitelisted keys only — we don't allow setting tokens or
chat IDs at runtime, both for safety and because most are read at
import time and wouldn't take effect.

Resolution order (highest priority first):
1. Override (this file)
2. Env var
3. Caller-supplied default
"""

import json
import os

from config import STATE_DIR

OVERRIDES_PATH = os.path.join(STATE_DIR, "overrides.json")


def _coerce_int(s: str) -> int:
    return int(s)


def _coerce_str(s: str) -> str:
    return s


SETTABLE_KEYS = {
    "OPENROUTER_MODEL": _coerce_str,
    "QUIET_HOURS": _coerce_str,
    "REPORT_HOUR": _coerce_int,
    "REPORT_INTERVAL_HOURS": _coerce_int,
    "REPORTS_RETENTION_DAYS": _coerce_int,
    "TTS_MODEL": _coerce_str,
    "TTS_VOICE": _coerce_str,
    "TTS_AS_VOICE_MESSAGE": _coerce_str,
    "TTS_MAX_CHARS": _coerce_int,
    "TTS_SPEED": _coerce_str,
    "TTS_RESPONSE_FORMAT": _coerce_str,
    "TTS_PCM_SAMPLE_RATE": _coerce_int,
    "ABUSEIPDB_CACHE_TTL_HOURS": _coerce_int,
    "ABUSEIPDB_LOOKUP_LIMIT": _coerce_int,
    "DIGEST_MODE": _coerce_str,
}


def is_settable(key: str) -> bool:
    return key in SETTABLE_KEYS


def load_overrides() -> dict:
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    try:
        with open(OVERRIDES_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_overrides(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(OVERRIDES_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def set_override(key: str, value: str) -> str:
    """Validate and persist an override. Returns the stored value (after
    coercion). Raises ValueError on unknown key or bad value."""
    if not is_settable(key):
        raise ValueError(f"key not settable: {key}")
    coerce = SETTABLE_KEYS[key]
    try:
        coerced = coerce(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"bad value for {key}: {e}") from None
    data = load_overrides()
    data[key] = coerced
    save_overrides(data)
    return str(coerced)


def unset_override(key: str) -> bool:
    """Remove an override. Returns True if something was actually removed."""
    data = load_overrides()
    if key not in data:
        return False
    del data[key]
    save_overrides(data)
    return True


def effective(key: str, default: str | None = None) -> str | None:
    """Resolve a key: override → env → default."""
    overrides = load_overrides()
    if key in overrides:
        return str(overrides[key])
    return os.getenv(key, default)


def effective_int(key: str, default: int | None = None) -> int | None:
    raw = effective(key, None)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def report_config() -> list[dict]:
    """Snapshot current effective values for /config display."""
    data = load_overrides()
    rows = []
    for key in sorted(SETTABLE_KEYS):
        env_val = os.getenv(key)
        override_val = data.get(key)
        if override_val is not None:
            source = "override"
            value = str(override_val)
        elif env_val:
            source = "env"
            value = env_val
        else:
            source = "default"
            value = "(unset)"
        rows.append({"key": key, "value": value, "source": source})
    return rows
