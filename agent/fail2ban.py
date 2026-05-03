"""Fail2ban status reader.

Reads fail2ban's SQLite database directly (read-only, via URI) so we can
report bans-in-the-last-24h, top banned IPs, and per-jail counts without
needing the `fail2ban-client` binary inside our container. The DB lives
at /var/lib/fail2ban/fail2ban.sqlite3 on the host; we mount it at
/host/var/lib/fail2ban/.

If fail2ban isn't installed on the host (no DB file) we return
`{"enabled": False}` rather than erroring — fail2ban being absent is a
valid state, not a failure.

Note: we don't try to compute "currently banned" precisely. Bantime is
per-jail and stored in the jail's runtime config, not the DB. The
`bans` table records every ban-event historically; that's enough for a
useful "you got hit N times in the last day" signal.
"""

import os
import sqlite3
import time
from collections import Counter

from config import HOST_PREFIX

DEFAULT_DB_PATH = os.path.join(HOST_PREFIX, "var/lib/fail2ban/fail2ban.sqlite3")

WINDOW_24H = 24 * 3600
WINDOW_7D = 7 * 24 * 3600


def _open_readonly(path: str) -> sqlite3.Connection:
    """Open the DB read-only via URI so we can't possibly mutate the host file."""
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def _query_bans(db_path: str) -> list[tuple]:
    """Return [(ip, jail, timeofban), ...] from the bans table."""
    with _open_readonly(db_path) as conn:
        cur = conn.execute("SELECT ip, jail, timeofban FROM bans")
        return cur.fetchall()


def _summarize(rows: list[tuple], now_ts: float) -> dict:
    cutoff_24h = now_ts - WINDOW_24H
    cutoff_7d = now_ts - WINDOW_7D

    bans_24h = [r for r in rows if r[2] >= cutoff_24h]
    bans_7d = [r for r in rows if r[2] >= cutoff_7d]

    top_ips_24h = Counter(r[0] for r in bans_24h).most_common(5)
    top_jails_24h = Counter(r[1] for r in bans_24h).most_common(5)

    recent = sorted(rows, key=lambda r: r[2], reverse=True)[:5]
    recent_sample = [
        {"ip": ip, "jail": jail, "timeofban": int(ts)}
        for ip, jail, ts in recent
    ]

    return {
        "enabled": True,
        "bans_24h": len(bans_24h),
        "bans_7d": len(bans_7d),
        "top_banned_ips_24h": top_ips_24h,
        "top_jails_24h": top_jails_24h,
        "recent_sample": recent_sample,
    }


def get_status(db_path: str | None = None, now_ts: float | None = None) -> dict:
    """Return a snapshot of fail2ban activity.

    Args:
      db_path: override DB location (used in tests). Defaults to the host mount.
      now_ts: override 'now' (used in tests). Defaults to time.time().
    """
    path = db_path or DEFAULT_DB_PATH
    if not os.path.exists(path):
        return {"enabled": False, "reason": f"db not found at {path}"}

    try:
        rows = _query_bans(path)
    except sqlite3.Error as e:
        return {"enabled": False, "reason": f"sqlite error: {e}"}
    except Exception as e:
        return {"enabled": False, "reason": f"unexpected error: {e}"}

    return _summarize(rows, now_ts if now_ts is not None else time.time())


if __name__ == "__main__":
    import pprint
    pprint.pp(get_status())
