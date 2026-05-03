"""Enumerate ackable findings from the security scan + system health output.

A 'finding' is any specific, snoozable observation surfaced by the agent —
e.g. "new cron file at /etc/cron.d/foo" or "container X is unhealthy".
Each has a stable fingerprint (see acks.fingerprint) so a user can snooze
it with a button tap and the next digest skips it until expiry.

This module is the single source of truth for what's ackable. Both the
post-digest button messages and the pre-prompt data filter walk the same
schema below.
"""

import copy
from dataclasses import dataclass

from acks import fingerprint


@dataclass
class Finding:
    category: str        # e.g. "cron_new", "container_concerning"
    key: str             # e.g. "/etc/cron.d/foo", "network-agent"
    label: str           # human-readable, used as button-message text
    severity: str        # "critical" | "warning"
    fingerprint: str     # acks.fingerprint(category, key)


def _f(category: str, key: str, label: str, severity: str) -> Finding:
    return Finding(category, key, label, severity, fingerprint(category, key))


def enumerate_findings(security: dict, health: dict) -> list[Finding]:
    """Walk security + health dicts, return a flat list of ackable findings."""
    findings: list[Finding] = []

    # --- Security scan deltas ---
    deltas = (security or {}).get("deltas") or {}
    for category in ("authorized_keys", "cron", "systemd"):
        cat = deltas.get(category) or {}
        for path in cat.get("new", []):
            findings.append(_f(f"{category}_new", path,
                               f"🚨 New {category}: <code>{path}</code>", "critical"))
        for path in cat.get("modified", []):
            findings.append(_f(f"{category}_modified", path,
                               f"🚨 Modified {category}: <code>{path}</code>", "critical"))
        for path in cat.get("removed", []):
            findings.append(_f(f"{category}_removed", path,
                               f"⚠️ Removed {category}: <code>{path}</code>", "warning"))

    port_delta = deltas.get("listening_ports") or {}
    for port in port_delta.get("new", []):
        findings.append(_f("listening_port_new", str(port),
                           f"🚨 New listening port: <code>{port}</code>", "critical"))

    if security and security.get("ld_so_preload_changed"):
        findings.append(_f("ld_so_preload_changed", "",
                           "🚨 <code>/etc/ld.so.preload</code> changed", "critical"))

    for proc in (security or {}).get("suspicious_processes", []) or []:
        key = f"{proc.get('exe', '')}:{proc.get('pid', '')}"
        findings.append(_f("suspicious_proc", key,
                           f"🚨 Suspicious process: <code>{proc.get('exe')}</code> "
                           f"(pid {proc.get('pid')})", "critical"))

    # --- System health ---
    docker = (health or {}).get("docker_containers") or {}
    for c in docker.get("concerning", []) or []:
        name = c.get("name", "")
        findings.append(_f("container_concerning", name,
                           f"⚠️ Concerning container: <code>{name}</code> "
                           f"({c.get('status')}, health={c.get('health')})", "warning"))
    for c in docker.get("high_restart", []) or []:
        name = c.get("name", "")
        findings.append(_f("container_high_restart", name,
                           f"⚠️ High restart count: <code>{name}</code> "
                           f"({c.get('restart_count')} restarts)", "warning"))

    if (health or {}).get("reboot_required", {}).get("required"):
        findings.append(_f("reboot_required", "",
                           "⚠️ Reboot required (kernel update pending)", "warning"))

    pending = (health or {}).get("pending_updates") or {}
    if pending.get("security", 0) > 0:
        n = pending["security"]
        findings.append(_f("security_updates_pending", "",
                           f"⚠️ {n} pending security update(s)", "warning"))

    return findings


def filter_unsnoozed(findings: list[Finding], snoozed: set[str]) -> list[Finding]:
    return [f for f in findings if f.fingerprint not in snoozed]


def strip_snoozed_from_data(security: dict, health: dict, snoozed: set[str]) -> tuple[dict, dict]:
    """Return deep copies of security/health with snoozed findings removed,
    so the AI prompt only sees what hasn't been silenced.

    Mirrors enumerate_findings — same schema, same fingerprints. Both walks
    must stay in sync.
    """
    sec = copy.deepcopy(security or {})
    hlth = copy.deepcopy(health or {})

    deltas = sec.get("deltas") or {}
    for category in ("authorized_keys", "cron", "systemd"):
        cat = deltas.get(category)
        if not isinstance(cat, dict):
            continue
        cat["new"] = [p for p in cat.get("new", [])
                      if fingerprint(f"{category}_new", p) not in snoozed]
        cat["modified"] = [p for p in cat.get("modified", [])
                           if fingerprint(f"{category}_modified", p) not in snoozed]
        cat["removed"] = [p for p in cat.get("removed", [])
                          if fingerprint(f"{category}_removed", p) not in snoozed]

    port_delta = deltas.get("listening_ports")
    if isinstance(port_delta, dict):
        port_delta["new"] = [p for p in port_delta.get("new", [])
                             if fingerprint("listening_port_new", str(p)) not in snoozed]

    if sec.get("ld_so_preload_changed") and fingerprint("ld_so_preload_changed", "") in snoozed:
        sec["ld_so_preload_changed"] = False

    sec["suspicious_processes"] = [
        proc for proc in sec.get("suspicious_processes", []) or []
        if fingerprint("suspicious_proc",
                       f"{proc.get('exe', '')}:{proc.get('pid', '')}") not in snoozed
    ]

    docker = hlth.get("docker_containers")
    if isinstance(docker, dict):
        docker["concerning"] = [
            c for c in docker.get("concerning", []) or []
            if fingerprint("container_concerning", c.get("name", "")) not in snoozed
        ]
        docker["high_restart"] = [
            c for c in docker.get("high_restart", []) or []
            if fingerprint("container_high_restart", c.get("name", "")) not in snoozed
        ]

    if hlth.get("reboot_required", {}).get("required") and \
            fingerprint("reboot_required", "") in snoozed:
        hlth["reboot_required"] = {"required": False, "packages": []}

    pending = hlth.get("pending_updates")
    if isinstance(pending, dict) and pending.get("security", 0) > 0 and \
            fingerprint("security_updates_pending", "") in snoozed:
        pending["security"] = 0
        pending["security_packages"] = []

    return sec, hlth
