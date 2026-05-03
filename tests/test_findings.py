from acks import fingerprint
from findings import enumerate_findings, filter_unsnoozed, strip_snoozed_from_data


def _security_with_findings():
    return {
        "deltas": {
            "authorized_keys": {"new": ["/root/.ssh/authorized_keys"], "modified": [], "removed": []},
            "cron": {"new": ["/etc/cron.d/foo", "/etc/cron.d/bar"], "modified": [], "removed": []},
            "systemd": {"new": [], "modified": ["/etc/systemd/system/x.service"], "removed": []},
            "listening_ports": {"new": [8080], "removed": []},
        },
        "ld_so_preload": "/tmp/evil.so",
        "ld_so_preload_changed": True,
        "suspicious_processes": [
            {"pid": 1234, "exe": "/tmp/abc", "cmdline": "/tmp/abc --evil"},
        ],
    }


def _health_with_findings():
    return {
        "reboot_required": {"required": True, "packages": ["linux-image-amd64"]},
        "pending_updates": {"total": 5, "security": 2, "security_packages": ["openssl", "curl"]},
        "docker_containers": {
            "concerning": [{"name": "broken-svc", "status": "exited", "exit_code": 1, "health": None,
                            "restart_count": 0, "image": "x", "image_age_days": 5}],
            "high_restart": [{"name": "loopy", "status": "running", "exit_code": 0, "health": None,
                              "restart_count": 12, "image": "y", "image_age_days": 10}],
            "stale_images_90d": [],
            "all_containers": [],
            "total": 2,
            "running": 1,
        },
    }


def test_enumerate_findings_covers_all_categories():
    findings = enumerate_findings(_security_with_findings(), _health_with_findings())
    cats = {f.category for f in findings}
    assert "authorized_keys_new" in cats
    assert "cron_new" in cats
    assert "systemd_modified" in cats
    assert "listening_port_new" in cats
    assert "ld_so_preload_changed" in cats
    assert "suspicious_proc" in cats
    assert "container_concerning" in cats
    assert "container_high_restart" in cats
    assert "reboot_required" in cats
    assert "security_updates_pending" in cats


def test_enumerate_handles_empty_dicts():
    assert enumerate_findings({}, {}) == []
    assert enumerate_findings(None, None) == []


def test_each_finding_has_unique_fingerprint():
    findings = enumerate_findings(_security_with_findings(), _health_with_findings())
    fps = [f.fingerprint for f in findings]
    assert len(fps) == len(set(fps)), "fingerprints collide"


def test_filter_unsnoozed_removes_only_snoozed():
    findings = enumerate_findings(_security_with_findings(), _health_with_findings())
    snoozed = {findings[0].fingerprint}
    remaining = filter_unsnoozed(findings, snoozed)
    assert len(remaining) == len(findings) - 1
    assert findings[0].fingerprint not in {f.fingerprint for f in remaining}


def test_strip_snoozed_removes_cron_entry():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("cron_new", "/etc/cron.d/foo")}
    sec2, health2 = strip_snoozed_from_data(sec, health, snoozed)
    assert "/etc/cron.d/foo" not in sec2["deltas"]["cron"]["new"]
    assert "/etc/cron.d/bar" in sec2["deltas"]["cron"]["new"]
    # Original is untouched (deepcopy)
    assert "/etc/cron.d/foo" in sec["deltas"]["cron"]["new"]


def test_strip_snoozed_removes_listening_port():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("listening_port_new", "8080")}
    sec2, _ = strip_snoozed_from_data(sec, health, snoozed)
    assert sec2["deltas"]["listening_ports"]["new"] == []


def test_strip_snoozed_clears_ld_so_preload_flag():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("ld_so_preload_changed", "")}
    sec2, _ = strip_snoozed_from_data(sec, health, snoozed)
    assert sec2["ld_so_preload_changed"] is False


def test_strip_snoozed_clears_reboot_required():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("reboot_required", "")}
    _, health2 = strip_snoozed_from_data(sec, health, snoozed)
    assert health2["reboot_required"]["required"] is False


def test_strip_snoozed_zeros_security_updates_count():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("security_updates_pending", "")}
    _, health2 = strip_snoozed_from_data(sec, health, snoozed)
    assert health2["pending_updates"]["security"] == 0
    assert health2["pending_updates"]["security_packages"] == []


def test_strip_snoozed_removes_concerning_container():
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("container_concerning", "broken-svc")}
    _, health2 = strip_snoozed_from_data(sec, health, snoozed)
    assert health2["docker_containers"]["concerning"] == []


def test_strip_with_no_snoozed_is_identity():
    sec = _security_with_findings()
    health = _health_with_findings()
    sec2, health2 = strip_snoozed_from_data(sec, health, set())
    assert sec2 == sec
    assert health2 == health


def test_enumerate_findings_after_strip_yields_remainder():
    """End-to-end: strip snoozed, re-enumerate, snoozed entries gone."""
    sec = _security_with_findings()
    health = _health_with_findings()
    snoozed = {fingerprint("cron_new", "/etc/cron.d/foo")}
    sec2, health2 = strip_snoozed_from_data(sec, health, snoozed)
    findings = enumerate_findings(sec2, health2)
    cron_paths = [f.key for f in findings if f.category == "cron_new"]
    assert "/etc/cron.d/foo" not in cron_paths
    assert "/etc/cron.d/bar" in cron_paths
