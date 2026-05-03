import os
import re
import subprocess
from collections import Counter

from config import LOG_PATH


def _read_via_journalctl(hours: int) -> list[str]:
    result = subprocess.run(
        ["journalctl", "-u", "ssh", "--since", f"{hours} hours ago", "--no-pager"],
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.splitlines()


def _read_via_file() -> list[str]:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", errors="ignore") as f:
        return f.readlines()


def get_auth_log_summary(hours: int = 24) -> dict:
    """Read last N hours of auth events and return a summary."""
    try:
        lines = _read_via_journalctl(hours)
    except Exception as e:
        print(f"[logs] journalctl failed, falling back to file: {e}")
        lines = _read_via_file()

    failed = [l for l in lines if "Failed password" in l or "Invalid user" in l]
    accepted = [l for l in lines if "Accepted" in l]

    ips = re.findall(r"from (\d+\.\d+\.\d+\.\d+)", "\n".join(failed))
    top_ips = Counter(ips).most_common(5)

    return {
        "failed_attempts": len(failed),
        "successful_logins": len(accepted),
        "top_attacker_ips": top_ips,
        "raw_sample": failed[:5],
    }


if __name__ == "__main__":
    print(get_auth_log_summary())
