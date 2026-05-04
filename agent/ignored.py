"""Permanent finding suppression (no expiry).

Distinct from acks (snoozes), which silence a finding for a fixed
duration. Ignored findings stay suppressed until the user explicitly
removes them via /unignore. Useful for known false-positives or
accepted-risk items that should never re-alert.

Fingerprints are the same `agent/acks.fingerprint(category, key)` shape
so the same filter functions in `findings.py` work without modification.
"""

import json
import os
from datetime import datetime, timezone

from config import STATE_DIR

IGNORED_PATH = os.path.join(STATE_DIR, "ignored.json")


def _load() -> dict:
    if not os.path.exists(IGNORED_PATH):
        return {}
    try:
        with open(IGNORED_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(IGNORED_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def ignored_entries() -> dict:
    """Return the full {fp: {label, added_at, ...}} dict."""
    return _load()


def ignored_fingerprints() -> set[str]:
    """Just the fingerprint set, for filter functions in findings.py."""
    return set(_load().keys())


def is_ignored(fp: str) -> bool:
    return fp in _load()


def add_ignored(fp: str, label: str) -> dict:
    """Permanently mark `fp` as ignored. Idempotent — adding an existing
    fingerprint refreshes its label and `added_at`."""
    data = _load()
    entry = {
        "fingerprint": fp,
        "label": (label or "")[:200],
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    data[fp] = entry
    _save(data)
    return entry


def remove_ignored(fp: str) -> bool:
    """Stop ignoring `fp`. Returns True if something was actually removed."""
    data = _load()
    if fp not in data:
        return False
    del data[fp]
    _save(data)
    return True
