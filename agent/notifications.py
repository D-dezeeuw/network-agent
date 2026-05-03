"""Notification routing: mute, quiet hours, critical-chat split, alarm poller.

Three knobs the operator gets:

- `TELEGRAM_CRITICAL_CHAT_ID`: optional separate chat for critical events.
  When set, critical findings (and real-time Netdata CRITICAL alarms) are
  also sent here in addition to the routine digest channel.

- `QUIET_HOURS=22-7`: routine digests are suppressed during this window
  (server local time). Critical events still get through.

- `/mute_all <hours>`: explicit override that suppresses everything,
  including criticals, until expiry.

A real-time poller checks Netdata's active alarms once a minute and
sends new CRITICAL firings to the critical chat. Same alarm fingerprint
is throttled to at most once per 30 minutes.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from config import STATE_DIR
from netdata import fetch_active_alarms
from overrides import effective
from tg_publish import send_message

log = logging.getLogger("notifications")

TELEGRAM_CRITICAL_CHAT_ID = (os.getenv("TELEGRAM_CRITICAL_CHAT_ID") or "").strip() or None
QUIET_HOURS = (os.getenv("QUIET_HOURS") or "").strip()

MUTE_PATH = os.path.join(STATE_DIR, "mute.json")

ALARM_THROTTLE_S = 30 * 60      # don't re-fire the same alarm fingerprint within 30 min
POLL_INTERVAL_S = 60            # how often the poller checks Netdata alarms


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- mute persistence -------------------------------------------------------

def is_muted() -> bool:
    if not os.path.exists(MUTE_PATH):
        return False
    try:
        with open(MUTE_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    expires = data.get("expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(expires) > _now()
    except (ValueError, TypeError):
        return False


def mute_for(hours: float) -> dict:
    """Persist a mute until now + `hours`."""
    os.makedirs(STATE_DIR, exist_ok=True)
    expires = _now() + timedelta(hours=hours)
    data = {"set_at": _now().isoformat(), "expires_at": expires.isoformat()}
    with open(MUTE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    return data


def clear_mute() -> bool:
    if not os.path.exists(MUTE_PATH):
        return False
    try:
        os.remove(MUTE_PATH)
        return True
    except OSError:
        return False


def mute_status() -> dict | None:
    """Return the current mute record (with expiry) or None if not muted."""
    if not os.path.exists(MUTE_PATH):
        return None
    try:
        with open(MUTE_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    expires = data.get("expires_at")
    try:
        if expires and datetime.fromisoformat(expires) > _now():
            return data
    except (ValueError, TypeError):
        pass
    return None


# --- quiet hours ------------------------------------------------------------

def parse_quiet_hours(spec: str) -> tuple[int, int] | None:
    """Parse 'START-END' (each 0-23). Returns None if malformed."""
    if not spec or "-" not in spec:
        return None
    try:
        start_s, end_s = spec.split("-", 1)
        start = int(start_s.strip()) % 24
        end = int(end_s.strip()) % 24
    except ValueError:
        return None
    return start, end


def is_quiet_at(hour: int, spec: str | None = None) -> bool:
    """Pure-function variant that's easy to test for any hour."""
    parsed = parse_quiet_hours(spec if spec is not None else QUIET_HOURS)
    if not parsed:
        return False
    start, end = parsed
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraps midnight, e.g. 22-7 means 22, 23, 0, 1, ..., 6
    return hour >= start or hour < end


def is_quiet_now() -> bool:
    """Check the override-aware QUIET_HOURS spec, then evaluate against now."""
    spec = effective("QUIET_HOURS", QUIET_HOURS) or ""
    return is_quiet_at(datetime.now().hour, spec)


# --- routing decisions ------------------------------------------------------

def should_send_digest() -> tuple[bool, str]:
    """Routine-digest gate. Returns (allow, reason)."""
    if is_muted():
        return False, "muted"
    if is_quiet_now():
        return False, "quiet hours"
    return True, "ok"


def critical_chat_id() -> str | None:
    return TELEGRAM_CRITICAL_CHAT_ID


def send_to_critical(text: str) -> bool:
    """Send `text` to the critical chat, respecting mute (but not quiet hours)."""
    if is_muted():
        return False
    chat = critical_chat_id()
    if not chat:
        return False
    return send_message(text, chat_id=chat)


# --- alarm poller -----------------------------------------------------------

CRITICAL_STATUSES = {"CRITICAL", "CRIT"}


def _alarm_fingerprint(alarm: dict) -> str:
    name = alarm.get("name", "?")
    chart = alarm.get("chart", "?")
    status = alarm.get("status", "?")
    return f"{name}|{chart}|{status}"


def _is_critical(alarm: dict) -> bool:
    return (alarm.get("status") or "").upper() in CRITICAL_STATUSES


def _format_alarm(alarm: dict) -> str:
    name = alarm.get("name", "alarm")
    chart = alarm.get("chart", "")
    status = alarm.get("status", "")
    info = alarm.get("info") or alarm.get("summary") or ""
    value = alarm.get("value")
    units = alarm.get("units", "")
    parts = [f"🚨 <b>{name}</b> [{status}]"]
    if chart:
        parts.append(f"chart: <code>{chart}</code>")
    if value is not None:
        parts.append(f"value: <code>{value}{units}</code>")
    if info:
        parts.append(str(info))
    return "\n".join(parts)


def prune_seen(seen: dict[str, datetime], now: datetime) -> dict[str, datetime]:
    cutoff = now - timedelta(seconds=ALARM_THROTTLE_S)
    return {fp: ts for fp, ts in seen.items() if ts > cutoff}


def select_alarms_to_send(alarms: list[dict], seen: dict[str, datetime],
                          now: datetime) -> list[tuple[str, dict]]:
    """Return list of (fingerprint, alarm) tuples that should be sent now —
    critical, not in throttle window. Pure function for testability."""
    out = []
    for alarm in alarms or []:
        if not _is_critical(alarm):
            continue
        fp = _alarm_fingerprint(alarm)
        if fp in seen:
            continue
        out.append((fp, alarm))
    return out


async def alarm_poller_loop() -> None:
    """Long-running task: every POLL_INTERVAL_S, check Netdata alarms and
    send new criticals to the critical chat."""
    if not critical_chat_id():
        log.info("alarm poller: TELEGRAM_CRITICAL_CHAT_ID unset; poller idle")
        return
    seen: dict[str, datetime] = {}
    log.info("alarm poller: starting (throttle %ds, interval %ds)",
             ALARM_THROTTLE_S, POLL_INTERVAL_S)
    while True:
        try:
            alarms = await asyncio.to_thread(fetch_active_alarms)
            now = _now()
            seen = prune_seen(seen, now)
            for fp, alarm in select_alarms_to_send(alarms, seen, now):
                if is_muted():
                    seen[fp] = now  # mark as seen so we don't flood after unmute
                    continue
                send_to_critical(_format_alarm(alarm))
                seen[fp] = now
                log.info("alarm poller: emitted %s", fp)
        except Exception:
            log.exception("alarm poller iteration failed")
        await asyncio.sleep(POLL_INTERVAL_S)
