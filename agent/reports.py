"""Per-cycle report archive.

Every digest cycle (excluding /preview) writes a JSON record to
/state/reports/<utc-timestamp>.json capturing everything that fed into
the digest plus the digest text itself. Subsequent commands and Q&A
tools read these files to answer historical questions ("how many
criticals last week?", "graph fail2ban bans over 30 days").

Schema is versioned (`schema_version: 1`). Readers tolerate missing
keys via .get() — when we add fields, old files keep parsing.

Intentionally separate from agent/trends.py: trends snapshots are a
small numeric subset used for inline digest annotations and live
sparklines; reports are the full historical record. They co-exist for
now; once reports has been running long enough we may dedupe.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from glob import glob

from config import STATE_DIR

log = logging.getLogger("reports")

REPORTS_DIR = os.path.join(STATE_DIR, "reports")
SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _ts_from_filename(path: str) -> datetime | None:
    """Parse the YYYYMMDDTHHMMSSZ stem back into a UTC datetime."""
    try:
        stem = os.path.basename(path).removesuffix(".json")
        return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# --- record building --------------------------------------------------------

def _safe_get(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _compute_derived(findings, system_health) -> dict:
    """Fields that aren't in any source dict but are useful for graphs.

    Pre-computing these at write time keeps `/history <metric>` rendering
    simple — no per-record arithmetic on every render.
    """
    findings_total = len(findings or [])
    findings_critical = sum(1 for f in (findings or []) if getattr(f, "severity", None) == "critical")
    findings_warning = sum(1 for f in (findings or []) if getattr(f, "severity", None) == "warning")
    docker = _safe_get(system_health, "docker_containers", default={}) or {}
    containers_concerning = len(docker.get("concerning") or [])
    containers_high_restart = len(docker.get("high_restart") or [])
    return {
        "findings_total": findings_total,
        "findings_critical": findings_critical,
        "findings_warning": findings_warning,
        "containers_concerning": containers_concerning,
        "containers_high_restart": containers_high_restart,
    }


def build_record(*, timestamp, trigger, model, cycle_duration_ms,
                 decision, digest_html, digest_parts,
                 findings, metrics, trends, security, system_health,
                 auth, fail2ban, rkhunter, raid, ip_reputations,
                 news, active_alarms, active_acks) -> dict:
    """Assemble a serializable report record from all the cycle inputs.

    Keyword-only on purpose — there are many fields and order would be
    a footgun when callers evolve.
    """
    section_lengths = {f"part_{i}": len(p or "") for i, p in enumerate(digest_parts or [])}
    findings_serialized = [
        {"severity": f.severity, "category": f.category, "key": f.key,
         "label": f.label, "fingerprint": f.fingerprint}
        for f in (findings or [])
    ]
    derived = _compute_derived(findings, system_health)
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": timestamp,
        "trigger": trigger,
        "model": model,
        "cycle_duration_ms": cycle_duration_ms,
        "decision": decision,
        "digest": {"html": digest_html, "section_lengths": section_lengths},
        "findings": findings_serialized,
        "derived": derived,
        "metrics": metrics or {},
        "trends": trends or {},
        "security_scan": security or {},
        "system_health": system_health or {},
        "auth": auth or {},
        "fail2ban": fail2ban or {},
        "rkhunter": rkhunter or {},
        "raid": raid or {},
        "ip_reputations": ip_reputations or {},
        "news": news or [],
        "active_alarms": active_alarms or [],
        "active_acks": active_acks or {},
    }


# --- I/O --------------------------------------------------------------------

def save_report(record: dict, when: datetime | None = None) -> str | None:
    """Write a report to /state/reports/. Returns path on success, None on failure.

    Failures are logged but never raised — a history-write failure must
    not break a digest cycle.
    """
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        name = f"{_stamp(when or _now())}.json"
        path = os.path.join(REPORTS_DIR, name)
        with open(path, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True, default=str)
        log.info("report saved to %s (%d bytes)", path, os.path.getsize(path))
        return path
    except OSError as e:
        log.warning("failed to save report: %s", e)
        return None


def list_report_paths() -> list[str]:
    """Return every report file path, sorted oldest-first by filename
    (which is the ISO timestamp)."""
    if not os.path.isdir(REPORTS_DIR):
        return []
    return sorted(glob(os.path.join(REPORTS_DIR, "*.json")))


def _load(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("failed to load %s: %s", path, e)
        return None


def load_reports(since: datetime | None = None,
                 limit: int | None = None) -> list[dict]:
    """Return reports oldest-first, optionally filtered by `since` (UTC) and
    capped at `limit` (most recent N)."""
    paths = list_report_paths()
    if since is not None:
        paths = [p for p in paths if (_ts_from_filename(p) or _now()) >= since]
    if limit is not None and len(paths) > limit:
        paths = paths[-limit:]
    out = []
    for p in paths:
        rec = _load(p)
        if rec is not None:
            out.append(rec)
    return out


def find_report_by_prefix(prefix: str) -> dict | None:
    """Return the most recent report whose filename stem starts with `prefix`.

    Used by /report — accepts ISO date (e.g. "2026-05-01") or an ISO
    timestamp prefix ("2026-05-01T08").
    """
    norm = prefix.replace("-", "").replace(":", "")
    candidates = [p for p in list_report_paths()
                  if os.path.basename(p).startswith(norm)]
    if not candidates:
        return None
    return _load(candidates[-1])


# --- pruning ----------------------------------------------------------------

def prune_old(keep_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete reports older than keep_days. Returns count removed."""
    cutoff = _now() - timedelta(days=keep_days)
    removed = 0
    for p in list_report_paths():
        ts = _ts_from_filename(p)
        if ts is None:
            continue
        if ts < cutoff:
            try:
                os.remove(p)
                removed += 1
            except OSError as e:
                log.warning("failed to delete %s: %s", p, e)
    if removed:
        log.info("pruned %d old report(s) (keep_days=%d)", removed, keep_days)
    return removed


# --- extraction & summaries -------------------------------------------------

def extract_path(record: dict, dotted_path: str, default=None):
    """Navigate a dotted-key path into a nested dict.

    Used by history_metrics to pull e.g. 'fail2ban.bans_24h' out of a
    record. Returns `default` on any missing segment or non-dict node.
    """
    cur = record
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def summarize_for_table(record: dict) -> dict:
    """One row's worth of `/history` text-table data."""
    decision = record.get("decision") or {}
    derived = record.get("derived") or {}
    fail2ban = record.get("fail2ban") or {}
    return {
        "timestamp": record.get("timestamp"),
        "verdict": _verdict_glyph(derived),
        "findings_total": derived.get("findings_total", 0),
        "findings_critical": derived.get("findings_critical", 0),
        "bans_24h": fail2ban.get("bans_24h", 0) if fail2ban.get("enabled") else None,
        "digest_sent": decision.get("digest_sent", False),
        "suppression_reason": decision.get("suppression_reason"),
    }


def _verdict_glyph(derived: dict) -> str:
    """Best-effort severity glyph from the derived counts (no AI text needed)."""
    if derived.get("findings_critical", 0) > 0:
        return "🚨"
    if derived.get("findings_warning", 0) > 0 or derived.get("findings_total", 0) > 0:
        return "⚠️"
    return "✅"


def aggregate_stats(records: list[dict]) -> dict:
    """Compute totals and means over a slice of records for /stats.

    Skips missing values silently — early records may not have every
    field, especially across schema bumps.
    """
    if not records:
        return {"records": 0}

    total_findings = 0
    total_critical = 0
    total_warning = 0
    total_probes = 0
    total_failed_auth = 0
    total_bans = 0
    digest_sent_count = 0
    cpu_vals = []
    ram_vals = []
    category_counts: dict[str, int] = {}

    for r in records:
        derived = r.get("derived") or {}
        total_findings += derived.get("findings_total", 0) or 0
        total_critical += derived.get("findings_critical", 0) or 0
        total_warning += derived.get("findings_warning", 0) or 0

        for f in r.get("findings") or []:
            cat = f.get("category")
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1

        auth = r.get("auth") or {}
        total_probes += auth.get("port_probes", 0) or 0
        total_failed_auth += auth.get("failed_attempts", 0) or 0

        f2b = r.get("fail2ban") or {}
        if f2b.get("enabled"):
            total_bans += f2b.get("bans_24h", 0) or 0

        if (r.get("decision") or {}).get("digest_sent"):
            digest_sent_count += 1

        cpu = extract_path(r, "metrics.cpu.avg")
        if isinstance(cpu, (int, float)):
            cpu_vals.append(cpu)
        ram = extract_path(r, "metrics.ram.avg")
        if isinstance(ram, (int, float)):
            ram_vals.append(ram)

    top_categories = sorted(category_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "records": len(records),
        "first_timestamp": records[0].get("timestamp"),
        "last_timestamp": records[-1].get("timestamp"),
        "findings_total": total_findings,
        "findings_critical": total_critical,
        "findings_warning": total_warning,
        "port_probes_total": total_probes,
        "failed_auth_total": total_failed_auth,
        "fail2ban_bans_total": total_bans,
        "digest_sent_count": digest_sent_count,
        "digest_sent_pct": round(digest_sent_count / len(records) * 100, 1),
        "cpu_avg_mean": round(sum(cpu_vals) / len(cpu_vals), 2) if cpu_vals else None,
        "ram_avg_mean": round(sum(ram_vals) / len(ram_vals), 2) if ram_vals else None,
        "top_finding_categories": top_categories,
    }
