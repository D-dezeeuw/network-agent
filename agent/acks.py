"""Acknowledgement / snooze persistence.

Each finding has a stable fingerprint (md5(category|key) truncated to 8 chars).
Snoozing records the fingerprint with an expiry timestamp; expired entries are
pruned on every read so the JSON file doesn't grow unbounded.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from config import STATE_DIR

ACKS_PATH = os.path.join(STATE_DIR, "acks.json")


def fingerprint(category: str, key: str) -> str:
    """Stable short hash of (category, key). Used as both the dict key and
    the callback_data identifier (Telegram limits callback_data to 64 bytes,
    so we keep this short)."""
    return hashlib.md5(f"{category}|{key}".encode()).hexdigest()[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_raw() -> dict:
    if not os.path.exists(ACKS_PATH):
        return {}
    try:
        with open(ACKS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_raw(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ACKS_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _is_expired(entry: dict, now: datetime) -> bool:
    expires = entry.get("expires_at")
    if not expires:
        return True
    try:
        return datetime.fromisoformat(expires) <= now
    except ValueError:
        return True


def active_acks() -> dict:
    """Returns currently-active acks, pruning expired entries to disk."""
    raw = _load_raw()
    now = _now()
    active = {fp: e for fp, e in raw.items() if not _is_expired(e, now)}
    if len(active) != len(raw):
        _save_raw(active)
    return active


def is_snoozed(fp: str) -> bool:
    return fp in active_acks()


def snoozed_fingerprints() -> set[str]:
    return set(active_acks().keys())


def add_ack(fp: str, label: str, hours: int) -> dict:
    """Snooze a finding for `hours`. Returns the saved entry."""
    acks = active_acks()
    now = _now()
    entry = {
        "fingerprint": fp,
        "label": label[:200],
        "added_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=hours)).isoformat(),
    }
    acks[fp] = entry
    _save_raw(acks)
    return entry


def remove_ack(fp: str) -> bool:
    """Remove a snooze. Returns True if something was actually removed."""
    raw = _load_raw()
    if fp not in raw:
        return False
    del raw[fp]
    _save_raw(raw)
    return True
