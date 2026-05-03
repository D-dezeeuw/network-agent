import json
from datetime import datetime, timezone
from types import SimpleNamespace

import reports


def _set_reports_dir(monkeypatch, tmp_path):
    p = tmp_path / "reports"
    monkeypatch.setattr(reports, "REPORTS_DIR", str(p))
    return p


def _make_record(timestamp, **overrides):
    """Compact factory for synthetic report records."""
    base = {
        "schema_version": 1,
        "timestamp": timestamp,
        "trigger": "scheduled",
        "model": "test/model",
        "cycle_duration_ms": 1000,
        "decision": {"digest_sent": True, "suppression_reason": None,
                     "forced": False, "tool_mutes_active": {}, "global_mute": None},
        "digest": {"html": f"<b>digest at {timestamp}</b>", "section_lengths": {}},
        "findings": [],
        "derived": {"findings_total": 0, "findings_critical": 0,
                    "findings_warning": 0, "containers_concerning": 0,
                    "containers_high_restart": 0},
        "metrics": {},
        "trends": {},
        "security_scan": {},
        "system_health": {},
        "auth": {},
        "fail2ban": {"enabled": False},
        "news": [],
        "active_alarms": [],
        "active_acks": {},
    }
    base.update(overrides)
    return base


def test_save_and_load(monkeypatch, tmp_path):
    p = _set_reports_dir(monkeypatch, tmp_path)
    rec = _make_record("2026-05-03T08:00:00+00:00")
    when = datetime(2026, 5, 3, 8, 0, 0, tzinfo=timezone.utc)
    path = reports.save_report(rec, when=when)
    assert path is not None
    assert path.endswith("20260503T080000Z.json")
    loaded = reports.load_reports()
    assert len(loaded) == 1
    assert loaded[0]["timestamp"] == rec["timestamp"]


def test_save_failure_returns_none_doesnt_raise(monkeypatch):
    """A history-write failure must not break a digest cycle."""
    monkeypatch.setattr(reports, "REPORTS_DIR", "/nonexistent/reports/dir")
    monkeypatch.setattr(reports.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    result = reports.save_report({"x": 1})
    assert result is None  # logged warning, no raise


def test_load_reports_filters_by_since(monkeypatch, tmp_path):
    _set_reports_dir(monkeypatch, tmp_path)
    for hour in (6, 8, 10):
        when = datetime(2026, 5, 3, hour, 0, 0, tzinfo=timezone.utc)
        reports.save_report(_make_record(when.isoformat()), when=when)
    cutoff = datetime(2026, 5, 3, 9, 0, 0, tzinfo=timezone.utc)
    recent = reports.load_reports(since=cutoff)
    assert len(recent) == 1
    assert "T10:00" in recent[0]["timestamp"]


def test_load_reports_caps_with_limit(monkeypatch, tmp_path):
    _set_reports_dir(monkeypatch, tmp_path)
    for hour in (6, 7, 8, 9, 10):
        when = datetime(2026, 5, 3, hour, 0, 0, tzinfo=timezone.utc)
        reports.save_report(_make_record(when.isoformat()), when=when)
    capped = reports.load_reports(limit=2)
    assert len(capped) == 2
    # Limit returns the most recent, not the oldest
    assert "T09:00" in capped[0]["timestamp"]
    assert "T10:00" in capped[1]["timestamp"]


def test_load_reports_skips_corrupt_files(monkeypatch, tmp_path):
    p = _set_reports_dir(monkeypatch, tmp_path)
    when = datetime(2026, 5, 3, 8, 0, 0, tzinfo=timezone.utc)
    reports.save_report(_make_record("ok"), when=when)
    # Drop a junk file with valid filename
    junk = p / "20260504T080000Z.json"
    junk.write_text("{not valid json")
    loaded = reports.load_reports()
    assert len(loaded) == 1
    assert loaded[0]["timestamp"] == "ok"


def test_find_report_by_prefix_iso_date(monkeypatch, tmp_path):
    _set_reports_dir(monkeypatch, tmp_path)
    for d in (1, 2, 3):
        when = datetime(2026, 5, d, 8, 0, 0, tzinfo=timezone.utc)
        reports.save_report(_make_record(f"day-{d}"), when=when)
    found = reports.find_report_by_prefix("2026-05-02")
    assert found is not None
    assert found["timestamp"] == "day-2"


def test_find_report_by_prefix_handles_no_match(monkeypatch, tmp_path):
    _set_reports_dir(monkeypatch, tmp_path)
    when = datetime(2026, 5, 3, 8, 0, 0, tzinfo=timezone.utc)
    reports.save_report(_make_record("only-one"), when=when)
    assert reports.find_report_by_prefix("1999-01-01") is None


def test_prune_old_removes_old_files(monkeypatch, tmp_path):
    _set_reports_dir(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    # 30 days ago
    old = now.replace(microsecond=0) - __import__("datetime").timedelta(days=30)
    fresh = now.replace(microsecond=0)
    reports.save_report(_make_record("old"), when=old)
    reports.save_report(_make_record("fresh"), when=fresh)
    removed = reports.prune_old(keep_days=7)
    assert removed == 1
    remaining = reports.load_reports()
    assert len(remaining) == 1
    assert remaining[0]["timestamp"] == "fresh"


def test_prune_old_skips_unparseable_filenames(monkeypatch, tmp_path):
    p = _set_reports_dir(monkeypatch, tmp_path)
    p.mkdir(exist_ok=True)
    (p / "weird-name.json").write_text("{}")
    # Should not crash
    assert reports.prune_old(keep_days=1) == 0


# --- extract_path ------------------------------------------------------------

def test_extract_path_navigates_nested():
    rec = {"a": {"b": {"c": 42}}}
    assert reports.extract_path(rec, "a.b.c") == 42


def test_extract_path_missing_returns_default():
    assert reports.extract_path({}, "x.y", default=None) is None
    assert reports.extract_path({"x": 1}, "x.y", default="d") == "d"


def test_extract_path_non_dict_segment():
    rec = {"a": [1, 2, 3]}  # list isn't dict — can't navigate further
    assert reports.extract_path(rec, "a.0") is None


# --- summarize_for_table -----------------------------------------------------

def test_summarize_returns_compact_row():
    rec = _make_record("2026-05-03T08:00",
                       derived={"findings_total": 5, "findings_critical": 2,
                                "findings_warning": 3, "containers_concerning": 0,
                                "containers_high_restart": 0},
                       fail2ban={"enabled": True, "bans_24h": 7})
    s = reports.summarize_for_table(rec)
    assert s["timestamp"] == "2026-05-03T08:00"
    assert s["verdict"] == "🚨"  # has criticals
    assert s["findings_total"] == 5
    assert s["findings_critical"] == 2
    assert s["bans_24h"] == 7


def test_summarize_verdict_warning_when_only_warnings():
    rec = _make_record("ts",
                       derived={"findings_total": 2, "findings_critical": 0,
                                "findings_warning": 2, "containers_concerning": 0,
                                "containers_high_restart": 0})
    assert reports.summarize_for_table(rec)["verdict"] == "⚠️"


def test_summarize_verdict_healthy_when_no_findings():
    rec = _make_record("ts")
    assert reports.summarize_for_table(rec)["verdict"] == "✅"


def test_summarize_bans_none_when_fail2ban_disabled():
    rec = _make_record("ts", fail2ban={"enabled": False})
    assert reports.summarize_for_table(rec)["bans_24h"] is None


# --- aggregate_stats ---------------------------------------------------------

def test_aggregate_empty_records():
    s = reports.aggregate_stats([])
    assert s == {"records": 0}


def test_aggregate_sums_and_means():
    rec1 = _make_record("ts1",
                        derived={"findings_total": 3, "findings_critical": 1,
                                 "findings_warning": 2, "containers_concerning": 0,
                                 "containers_high_restart": 0},
                        auth={"port_probes": 50, "failed_attempts": 10},
                        fail2ban={"enabled": True, "bans_24h": 4},
                        metrics={"cpu": {"avg": 20.0}, "ram": {"avg": 40.0}})
    rec2 = _make_record("ts2",
                        derived={"findings_total": 1, "findings_critical": 0,
                                 "findings_warning": 1, "containers_concerning": 0,
                                 "containers_high_restart": 0},
                        auth={"port_probes": 30, "failed_attempts": 5},
                        fail2ban={"enabled": True, "bans_24h": 2},
                        metrics={"cpu": {"avg": 30.0}, "ram": {"avg": 60.0}})
    s = reports.aggregate_stats([rec1, rec2])
    assert s["records"] == 2
    assert s["findings_total"] == 4
    assert s["findings_critical"] == 1
    assert s["port_probes_total"] == 80
    assert s["failed_auth_total"] == 15
    assert s["fail2ban_bans_total"] == 6
    assert s["digest_sent_count"] == 2
    assert s["digest_sent_pct"] == 100.0
    assert s["cpu_avg_mean"] == 25.0
    assert s["ram_avg_mean"] == 50.0


def test_aggregate_top_categories_descending():
    f = lambda cat: {"severity": "warning", "category": cat, "key": "k", "label": "l", "fingerprint": "fp"}
    rec1 = _make_record("ts1", findings=[f("cron_new"), f("cron_new"), f("container_concerning")])
    rec2 = _make_record("ts2", findings=[f("cron_new")])
    s = reports.aggregate_stats([rec1, rec2])
    top = s["top_finding_categories"]
    assert top[0] == ("cron_new", 3)
    assert top[1] == ("container_concerning", 1)


# --- build_record ------------------------------------------------------------

def test_build_record_uses_severity_from_finding_objects():
    """Finding-like objects (Finding dataclass) should be serialized with severity."""
    findings = [
        SimpleNamespace(severity="critical", category="cron_new", key="/tmp/x",
                        label="evil cron", fingerprint="abc12345"),
        SimpleNamespace(severity="warning", category="container_concerning",
                        key="plex", label="plex unhealthy", fingerprint="def67890"),
    ]
    rec = reports.build_record(
        timestamp="2026-05-03T08:00:00Z", trigger="scheduled", model="m",
        cycle_duration_ms=100,
        decision={"digest_sent": True}, digest_html="x", digest_parts=["x"],
        findings=findings, metrics={}, trends={}, security={},
        system_health={"docker_containers": {"concerning": [{"name": "plex"}]}},
        auth={}, fail2ban={}, rkhunter={}, news=[], active_alarms=[], active_acks={},
    )
    assert rec["schema_version"] == 1
    assert rec["derived"]["findings_total"] == 2
    assert rec["derived"]["findings_critical"] == 1
    assert rec["derived"]["containers_concerning"] == 1
    serialized = rec["findings"]
    assert serialized[0]["severity"] == "critical"
    assert serialized[0]["category"] == "cron_new"
    assert serialized[0]["fingerprint"] == "abc12345"
