"""rkhunter (Rootkit Hunter) log integration.

Read-only consumer of the rkhunter combined log produced by the user's
own cron job — we don't run rkhunter ourselves. Surfaces warning count,
last-modified timestamp, and a tail of the most recent warnings so the
digest can flag suspicious-file/file-property/kernel-module hits.

Mirrors the fail2ban pattern: if the log doesn't exist (no rkhunter
installed) we return `{"enabled": False}` rather than erroring.
"""

import os
from collections import deque
from datetime import datetime, timezone

from config import HOST_PREFIX

DEFAULT_LOG_PATH = os.path.join(
    HOST_PREFIX, "logs/rkhunter/reports/rkhunter-combined.log"
)
RECENT_WARNINGS_TAIL = 10


def get_status(log_path: str | None = None) -> dict:
    """Summarize the rkhunter combined log.

    Streams the file line-by-line so a multi-MB log doesn't blow up
    memory. Counts every line containing "Warning" (matches the user's
    existing `grep -c "Warning"` cron one-liner) and keeps the last
    `RECENT_WARNINGS_TAIL` of them for context.
    """
    path = log_path or os.getenv("RKHUNTER_LOG_PATH", DEFAULT_LOG_PATH)

    if not os.path.exists(path):
        return {
            "enabled": False,
            "log_path": path,
            "reason": "log file not found",
        }

    try:
        stat = os.stat(path)
    except OSError as e:
        return {
            "enabled": False,
            "log_path": path,
            "reason": f"stat failed: {e}",
        }

    last_modified = datetime.fromtimestamp(
        stat.st_mtime, tz=timezone.utc
    ).isoformat()

    total = 0
    recent: deque = deque(maxlen=RECENT_WARNINGS_TAIL)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Warning" in line:
                    total += 1
                    recent.append(line.rstrip())
    except OSError as e:
        return {
            "enabled": True,
            "log_path": path,
            "total_warnings": 0,
            "recent_warnings": [],
            "last_modified": last_modified,
            "size_bytes": stat.st_size,
            "error": f"read failed: {e}",
        }

    return {
        "enabled": True,
        "log_path": path,
        "total_warnings": total,
        "recent_warnings": list(recent),
        "last_modified": last_modified,
        "size_bytes": stat.st_size,
    }
