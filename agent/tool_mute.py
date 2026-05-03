"""Per-tool mute — silence specific data sources for the next N digests.

Distinct from notifications.is_muted() (which silences ALL output). This
mutes one data category — e.g. 'news' or 'docker' — so the digest still
runs but that section is omitted.

Storage at /state/tool_mute.json maps alias → remaining-cycles. None or
a number ≥ 1. main.py calls decrement_counts() at the end of each digest
cycle so mutes auto-expire.
"""

import json
import os

from config import STATE_DIR

MUTE_PATH = os.path.join(STATE_DIR, "tool_mute.json")

# User-facing alias → internal data-source key. The internal key is what
# main.py / tools.py understand. Aliases are short for the user.
ALIASES = {
    "news": "news",
    "docker": "docker",
    "updates": "updates",
    "auth": "auth",
    "kernel": "kernel",
    "scan": "security_scan",
    "metrics": "metrics",
    "health": "system_health",
    "rkhunter": "rkhunter",
    "abuseipdb": "abuseipdb",
}


def known_aliases() -> list[str]:
    return sorted(ALIASES.keys())


def resolve(alias: str) -> str | None:
    """Map a user alias to its internal source key, or None if unknown."""
    return ALIASES.get(alias.lower().strip())


def _load() -> dict:
    if not os.path.exists(MUTE_PATH):
        return {}
    try:
        with open(MUTE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(MUTE_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def is_muted(source_key: str) -> bool:
    data = _load()
    if source_key not in data:
        return False
    val = data[source_key]
    if val is None:
        return True  # indefinite mute
    if isinstance(val, int):
        return val > 0
    return False


def mute(source_key: str, cycles: int | None = None) -> dict:
    """Mute `source_key`. If cycles is None, mute indefinitely; else for
    the given number of digest cycles."""
    data = _load()
    data[source_key] = cycles
    _save(data)
    return {"source": source_key, "cycles": cycles}


def unmute(source_key: str) -> bool:
    data = _load()
    if source_key not in data:
        return False
    del data[source_key]
    _save(data)
    return True


def decrement_counts() -> int:
    """Decrement every counted mute by 1; remove entries that hit zero.
    Indefinite mutes (None) are left alone. Returns count of expired keys.
    Called at the end of each digest cycle."""
    data = _load()
    expired = []
    for key, val in list(data.items()):
        if val is None:
            continue
        if not isinstance(val, int):
            expired.append(key)
            continue
        if val <= 1:
            expired.append(key)
        else:
            data[key] = val - 1
    for key in expired:
        del data[key]
    _save(data)
    return len(expired)


def active_mutes() -> dict:
    """Return current mute state (source → remaining cycles or None)."""
    return _load()
