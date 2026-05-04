"""Tool registry for interactive Q&A.

Each tool is a thin wrapper around an existing collector. Tools return
JSON-serializable data; the AI layer hands those results back to Claude
during the tool-call loop.
"""

from datetime import datetime, timedelta, timezone

from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from docker_logs import get_container_logs
import abuseipdb
from fail2ban import get_status as get_fail2ban_status
from raid import get_status as get_raid_status
from rkhunter import get_status as get_rkhunter_status
from logs import get_auth_log_summary
import reports
from security_news import fetch_security_news
from security_scan import run_scan
from system_health import (
    check_docker_containers,
    check_journal_errors,
    check_kernel_messages,
    check_pending_updates,
    check_reboot_required,
    run_health_check,
)


def _get_metrics(hours: int = 24) -> dict:
    after = -abs(hours) * 3600
    raw = collect_all_metrics()
    summary = {}
    for key, data in raw.items():
        if "data" in data:
            summary[key] = summarize_chart(data)
    summary["active_alarms"] = fetch_active_alarms()
    summary["window_hours"] = hours
    return summary


def _get_alarms() -> list:
    return fetch_active_alarms()


def _get_auth_log(hours: int = 24) -> dict:
    return get_auth_log_summary(hours=hours)


def _get_security_news() -> list:
    return fetch_security_news()


def _get_security_scan() -> dict:
    return run_scan(reset=False)


def _get_system_health() -> dict:
    return run_health_check()


def _get_docker_containers() -> dict:
    return check_docker_containers()


def _get_pending_updates() -> dict:
    return check_pending_updates()


def _get_reboot_required() -> dict:
    return check_reboot_required()


def _get_journal_errors(hours: int = 24) -> dict:
    return check_journal_errors(hours=hours)


def _get_kernel_messages(hours: int = 24) -> dict:
    return check_kernel_messages(hours=hours)


def _get_container_logs(name: str, tail: int = 100, since_minutes: int | None = None) -> dict:
    return get_container_logs(name=name, tail=tail, since_minutes=since_minutes)


def _get_fail2ban_status() -> dict:
    return get_fail2ban_status()


def _get_rkhunter_status() -> dict:
    return get_rkhunter_status()


def _get_abuseipdb_report(ip: str) -> dict:
    record = abuseipdb.lookup(ip)
    if record is None:
        return {"ip": ip, "available": False,
                "reason": "no API key set, invalid IP, or lookup failed"}
    return {"ip": ip, "available": True, **record}


def _get_raid_status_tool() -> dict:
    return get_raid_status()


def _get_report_history(days: int = 7, limit: int | None = None) -> list[dict]:
    """Return compact summaries (not full digests) of cycles in the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    records = reports.load_reports(since=since, limit=limit)
    return [reports.summarize_for_table(r) for r in records]


def _get_report_detail(timestamp: str) -> dict:
    """Return one full report by ISO date prefix or full timestamp."""
    record = reports.find_report_by_prefix(timestamp)
    return record if record is not None else {"error": f"no report matching {timestamp!r}"}


def _get_history_stats(days: int = 30) -> dict:
    """Return pre-aggregated counts/means over a rolling window."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    records = reports.load_reports(since=since)
    return reports.aggregate_stats(records)


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Return Netdata metric summaries (CPU, RAM, network, per-mount disk space) plus active alarms over the last N hours. Use for questions about resource usage, anomalies, or capacity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "default": 24, "description": "How far back to look, in hours."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alarms",
            "description": "Return Netdata alarms currently in non-CLEAR state. Use when the user asks specifically about active alerts/alarms.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_auth_log",
            "description": "Return SSH auth log summary: failed login count, successful logins, port-probe count (pre-auth connection lines that didn't even attempt credentials — the strongest 'we're being scanned' signal), top attacker IPs (failed-auth), top probe IPs (scan-only), and sample lines for each. Use for questions about login attempts, brute force, port scanning, who logged in.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "default": 24},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_security_news",
            "description": "Return CVE / security news entries filtered to the server's stack (Debian, Docker, nginx, kernel, Python). Use when asked about vulnerabilities, CVEs, or new advisories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_security_scan",
            "description": "Return the host security scan diff vs baseline: changes to authorized_keys, cron jobs, systemd units, ld.so.preload, listening ports, and any suspicious processes (running from /tmp, /var/tmp, /dev/shm, or with deleted exe). Use when asked about persistence, compromise, recent system changes, what's listening.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_health",
            "description": "Return the full system health snapshot: reboot-required flag, pending apt updates (incl. security count), recent journal errors, kernel warnings, Docker container state. Use for general 'how's the server' questions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_docker_containers",
            "description": "Return per-container Docker status: running count, `concerning` containers (unhealthy health check, dead, restart-looping, or crashed with non-zero exit code), `high_restart` containers (>3 restarts), `stale_images_90d` (image older than 90 days), and `all_containers` for the full list including clean-exited one-shot tasks. Use when asked about Docker, containers, or specific container names.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_updates",
            "description": "Return apt upgradable packages with security-update count broken out. Use when asked about updates, patches, or what packages need upgrading.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reboot_required",
            "description": "Return whether a host reboot is pending (e.g. after kernel update) and which packages triggered it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journal_errors",
            "description": "Return systemd journal entries at error level over the last N hours. Use to investigate broad-strokes 'what's been failing' questions.",
            "parameters": {
                "type": "object",
                "properties": {"hours": {"type": "integer", "default": 24}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kernel_messages",
            "description": "Return kernel-level warnings/errors filtered for OOM, I/O errors, segfaults, panics. Use for hardware/kernel-symptom questions.",
            "parameters": {
                "type": "object",
                "properties": {"hours": {"type": "integer", "default": 24}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_container_logs",
            "description": "Fetch recent stdout/stderr from a Docker container by name (exact or substring). Use this to debug 'why is X failing/restarting/erroring' questions — pull logs, then explain what's in them. Returns timestamped lines plus the container's current status. If no container matches the name, the response includes an `available` list of valid names. Substring matches are rejected if ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name (exact) or case-insensitive substring."},
                    "tail": {"type": "integer", "default": 100, "description": "Number of trailing log lines to fetch (max 500)."},
                    "since_minutes": {"type": "integer", "description": "Optional time window — only return lines newer than this many minutes."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fail2ban_status",
            "description": "Return fail2ban summary read from its SQLite DB: bans in the last 24h and 7d, top banned IPs, top jails, recent ban sample. Returns `enabled: false` if fail2ban isn't installed on the host. Use for questions about IP bans, brute-force mitigation, who got blocked recently.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rkhunter_status",
            "description": "Return rkhunter (Rootkit Hunter) summary parsed from its combined log file: total warning count, last-modified timestamp, and a tail of the most recent Warning lines. Returns `enabled: false` if the log file isn't present (rkhunter not installed or no scans run yet). Use for questions about rootkit scans, suspicious files, or 'what does rkhunter say'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_abuseipdb_report",
            "description": "Look up an IP's public abuse reputation via AbuseIPDB: confidence score 0-100, country, ISP, total reports, last-reported timestamp. Use for questions about a specific IP ('who's 1.2.3.4', 'why was this IP banned'). Returns `available: false` if no API key is set or the IP couldn't be resolved. Cache TTL is 24h by default so repeated questions don't burn quota.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IPv4 or IPv6 address to look up."},
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_raid_status",
            "description": "Return software-RAID (mdadm) status parsed from /proc/mdstat: per-array state pattern ([UU]/[U_]/[__]), member devices, and any in-progress rebuild/resync with percent + ETA. Severity is one of healthy / recovering / degraded. Use for 'is the raid healthy', 'how is the rebuild going', or 'which disk failed' questions. Returns `enabled: false` if /proc/mdstat isn't present.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report_history",
            "description": "Return COMPACT summaries (timestamp, verdict, finding/critical counts, ban count, sent y/n) of past digest cycles in the last N days. NOT the full digests — context-cheap. Use for 'what's been happening lately', 'how many criticals last week', 'when did we last have a quiet day' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 7, "description": "How far back to look."},
                    "limit": {"type": "integer", "description": "Cap on number of records returned."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report_detail",
            "description": "Return ONE full archived report by ISO date prefix (e.g. '2026-05-01') or timestamp prefix ('2026-05-01T08'). Use after calling get_report_history to drill into a specific cycle the user is asking about. Returns the digest text, full findings list, metrics, and all section data from that cycle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string", "description": "ISO date or timestamp prefix to match."},
                },
                "required": ["timestamp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_history_stats",
            "description": "Return pre-aggregated stats over the last N days: total/critical/warning finding counts, total port probes, total failed auth, total fail2ban bans, mean CPU/RAM, digest-sent ratio, top-5 finding categories. Use for 'how was the server this week/month' questions — saves the model from doing arithmetic over many records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 30},
                },
            },
        },
    },
]


TOOL_IMPLS = {
    "get_metrics": _get_metrics,
    "get_alarms": _get_alarms,
    "get_auth_log": _get_auth_log,
    "get_security_news": _get_security_news,
    "get_security_scan": _get_security_scan,
    "get_system_health": _get_system_health,
    "get_docker_containers": _get_docker_containers,
    "get_pending_updates": _get_pending_updates,
    "get_reboot_required": _get_reboot_required,
    "get_journal_errors": _get_journal_errors,
    "get_kernel_messages": _get_kernel_messages,
    "get_container_logs": _get_container_logs,
    "get_fail2ban_status": _get_fail2ban_status,
    "get_rkhunter_status": _get_rkhunter_status,
    "get_abuseipdb_report": _get_abuseipdb_report,
    "get_raid_status": _get_raid_status_tool,
    "get_report_history": _get_report_history,
    "get_report_detail": _get_report_detail,
    "get_history_stats": _get_history_stats,
}


def execute_tool(name: str, arguments: dict) -> dict | list:
    """Run a tool by name, returning its result or an error dict."""
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return impl(**(arguments or {}))
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
