"""Tool registry for interactive Q&A.

Each tool is a thin wrapper around an existing collector. Tools return
JSON-serializable data; the AI layer hands those results back to Claude
during the tool-call loop.
"""

from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from logs import get_auth_log_summary
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
            "description": "Return SSH auth log summary: failed login count, successful logins, top attacker IPs, sample of recent failures. Use for questions about login attempts, brute force, who logged in.",
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
