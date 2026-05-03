"""Auth log parser.

Reads sshd lines from journald (preferred) or a fallback log file and
extracts both authentication signals (failed/accepted) and *pre-auth*
probe signals — port-22 connections that don't even try a credential.
Pure port scanners hit a TCP banner and bail; they show up as
`Connection from`, `Did not receive identification string`, or
`Connection closed by ... [preauth]` lines without any later
`Failed password` or `Accepted` from the same session.

The probe count is the most useful new signal here — fail2ban can
already react to failed-auth bursts, but probe-only traffic slips past
that and is the strongest "you're being scanned" indicator we can read
without enabling firewall logging.
"""

import os
import re
import subprocess
from collections import Counter

from config import LOG_PATH

PROBE_PATTERNS = (
    "Connection from ",
    "Did not receive identification string from ",
    "Connection closed by ",  # often suffixed " [preauth]" — see _is_probe
    "Connection reset by ",
)


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


def _is_probe_line(line: str) -> bool:
    """A line is a 'probe' if it shows pre-auth disconnect / banner-only contact.

    `Connection closed by` and `Connection reset by` only count as probes
    when the [preauth] suffix is present — otherwise they're normal session
    teardowns after successful auth.
    """
    if "Connection from " in line or "Did not receive identification string from " in line:
        return True
    if ("Connection closed by " in line or "Connection reset by " in line) and "[preauth]" in line:
        return True
    return False


def _extract_ips(lines: list[str]) -> list[str]:
    """Pull the first IPv4 in each line. Good enough for sshd lines —
    they all carry the remote IP after `from` or `by`."""
    out = []
    for line in lines:
        m = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", line)
        if m:
            out.append(m.group(1))
    return out


def get_auth_log_summary(hours: int = 24) -> dict:
    """Read last N hours of auth events and return a summary."""
    try:
        lines = _read_via_journalctl(hours)
    except Exception as e:
        print(f"[logs] journalctl failed, falling back to file: {e}")
        lines = _read_via_file()

    failed = [l for l in lines if "Failed password" in l or "Invalid user" in l]
    accepted = [l for l in lines if "Accepted" in l]
    probes = [l for l in lines if _is_probe_line(l)]

    failed_ips = _extract_ips(failed)
    probe_ips = _extract_ips(probes)
    top_attacker_ips = Counter(failed_ips).most_common(5)
    top_probe_ips = Counter(probe_ips).most_common(5)

    return {
        "failed_attempts": len(failed),
        "successful_logins": len(accepted),
        "port_probes": len(probes),
        "top_attacker_ips": top_attacker_ips,
        "top_probe_ips": top_probe_ips,
        "raw_sample": failed[:5],
        "probe_sample": probes[:5],
    }


if __name__ == "__main__":
    import pprint
    pprint.pp(get_auth_log_summary())
