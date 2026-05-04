"""Metric registry for `/history <metric> [days]`.

Each entry maps a short user-facing alias to a dotted path inside the
report record (see agent/reports.py:extract_path) plus a chart label.
The registry is the single source of truth for what's plottable;
unknown aliases get a usage hint listing every option.

Adding a new graph = one new entry here. No bot-code change needed.
"""

from datetime import datetime, timezone

from reports import extract_path


METRICS: dict[str, dict] = {
    "fail2ban": {
        "path": "fail2ban.bans_24h",
        "label": "fail2ban bans (24h)",
        "title": "Fail2ban bans per cycle",
        "ylabel": "bans (last 24h)",
    },
    "probes": {
        "path": "auth.port_probes",
        "label": "port probes",
        "title": "SSH port probes per cycle",
        "ylabel": "probes (last 24h)",
    },
    "failed_auth": {
        "path": "auth.failed_attempts",
        "label": "failed auth attempts",
        "title": "Failed SSH auth per cycle",
        "ylabel": "attempts (last 24h)",
    },
    "findings": {
        "path": "derived.findings_total",
        "label": "active findings",
        "title": "Active findings per cycle",
        "ylabel": "count",
    },
    "criticals": {
        "path": "derived.findings_critical",
        "label": "critical findings",
        "title": "Critical findings per cycle",
        "ylabel": "count",
    },
    "warnings": {
        "path": "derived.findings_warning",
        "label": "warning findings",
        "title": "Warning findings per cycle",
        "ylabel": "count",
    },
    "cpu": {
        "path": "metrics.cpu.avg",
        "label": "CPU avg %",
        "title": "CPU average % per cycle",
        "ylabel": "%",
    },
    "ram": {
        "path": "metrics.ram.avg",
        "label": "RAM avg %",
        "title": "RAM average % per cycle",
        "ylabel": "%",
    },
    "containers": {
        "path": "derived.containers_concerning",
        "label": "concerning containers",
        "title": "Concerning containers per cycle",
        "ylabel": "count",
    },
    "updates": {
        "path": "system_health.pending_updates.security",
        "label": "pending security updates",
        "title": "Pending security updates per cycle",
        "ylabel": "count",
    },
    "rkhunter": {
        "path": "rkhunter.total_warnings",
        "label": "rkhunter warnings (cumulative)",
        "title": "rkhunter cumulative warning count per cycle",
        "ylabel": "warnings",
    },
    "raid": {
        "path": "raid.degraded_count",
        "label": "degraded RAID arrays",
        "title": "Degraded RAID array count per cycle",
        "ylabel": "arrays",
    },
}


def known_metrics() -> list[str]:
    return sorted(METRICS.keys())


def metric_info(metric: str) -> dict | None:
    return METRICS.get(metric.lower().strip())


def _parse_ts(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def series_for_metric(records: list[dict], metric: str) -> list[tuple[datetime, float]]:
    """Pull `(timestamp, value)` pairs out of records for `metric`.

    Records missing the value or with non-numeric data are skipped — not
    coerced to zero, since 'no data' and 'zero' carry different meaning
    (e.g. fail2ban not enabled vs. zero bans).
    """
    info = METRICS.get(metric)
    if info is None:
        return []
    points: list[tuple[datetime, float]] = []
    for r in records:
        ts = _parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        val = extract_path(r, info["path"])
        if isinstance(val, bool):  # bool is a subclass of int — skip explicitly
            continue
        if not isinstance(val, (int, float)):
            continue
        points.append((ts, float(val)))
    return points
