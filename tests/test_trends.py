import json
from datetime import datetime, timedelta, timezone

import trends


def _set_snapshots_dir(monkeypatch, tmp_path):
    p = tmp_path / "snapshots"
    monkeypatch.setattr(trends, "SNAPSHOTS_DIR", str(p))
    return p


# --- extract_snapshot --------------------------------------------------------

def test_extract_snapshot_pulls_scalars():
    metrics = {
        "cpu": {"avg": 23.5, "max": 50.0, "min": 5.0},
        "ram": {"avg": 60.1, "max": 70.0, "min": 50.0},
        "network": {"avg": 1234.0, "max": 9999.0, "min": 100.0},
    }
    health = {
        "pending_updates": {"total": 5, "security": 2},
        "docker_containers": {"concerning": [{"name": "x"}], "high_restart": []},
    }
    snap = trends.extract_snapshot(metrics, health)
    assert snap["cpu_avg"] == 23.5
    assert snap["ram_avg"] == 60.1
    assert snap["pending_total"] == 5
    assert snap["pending_security"] == 2
    assert snap["concerning_count"] == 1
    assert snap["high_restart_count"] == 0
    assert "timestamp" in snap


def test_extract_snapshot_pulls_disks():
    metrics = {
        "disk:/": {"avg": 45.0, "max": 50.0, "min": 40.0},
        "disk:/var/lib/docker": {"avg": 80.0, "max": 85.0, "min": 75.0},
        "ram": {"avg": 60.0},
    }
    snap = trends.extract_snapshot(metrics, {})
    assert snap["disks"] == {"disk:/": 45.0, "disk:/var/lib/docker": 80.0}


def test_extract_snapshot_handles_missing_data():
    snap = trends.extract_snapshot({}, {})
    assert snap["cpu_avg"] is None
    assert snap["pending_total"] == 0
    assert snap["concerning_count"] == 0
    assert snap["disks"] == {}


# --- save / load / prune -----------------------------------------------------

def test_save_and_list_snapshots(monkeypatch, tmp_path):
    _set_snapshots_dir(monkeypatch, tmp_path)
    trends.save_snapshot({"timestamp": "2026-05-01T08:00:00+00:00", "cpu_avg": 10})
    trends.save_snapshot({"timestamp": "2026-05-02T08:00:00+00:00", "cpu_avg": 20})
    paths = trends.list_snapshot_paths()
    assert len(paths) == 2
    # oldest first
    assert paths[0] < paths[1]


def test_load_recent_returns_snapshots_oldest_first(monkeypatch, tmp_path):
    _set_snapshots_dir(monkeypatch, tmp_path)
    trends.save_snapshot({"timestamp": "2026-05-01T08:00:00+00:00", "cpu_avg": 10})
    trends.save_snapshot({"timestamp": "2026-05-02T08:00:00+00:00", "cpu_avg": 20})
    snapshots = trends.load_recent()
    assert len(snapshots) == 2
    assert snapshots[0]["cpu_avg"] == 10
    assert snapshots[1]["cpu_avg"] == 20


def test_prune_keeps_only_n_newest(monkeypatch, tmp_path):
    p = _set_snapshots_dir(monkeypatch, tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    # Create 35 fake snapshot files with sortable filenames
    for i in range(35):
        fname = p / f"2026{i:04d}T000000Z.json"
        fname.write_text(json.dumps({"timestamp": f"2026-05-01T00:00:0{i % 10}+00:00", "cpu_avg": i}))
    removed = trends.prune_snapshots(keep=30)
    assert removed == 5
    assert len(trends.list_snapshot_paths()) == 30


def test_prune_no_op_under_threshold(monkeypatch, tmp_path):
    _set_snapshots_dir(monkeypatch, tmp_path)
    trends.save_snapshot({"timestamp": "2026-05-01T08:00:00+00:00", "cpu_avg": 10})
    assert trends.prune_snapshots(keep=30) == 0


def test_load_recent_skips_corrupt_files(monkeypatch, tmp_path):
    p = _set_snapshots_dir(monkeypatch, tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    (p / "20260501T000000Z.json").write_text(json.dumps({"timestamp": "x", "cpu_avg": 10}))
    (p / "20260502T000000Z.json").write_text("{not valid json")
    snapshots = trends.load_recent()
    assert len(snapshots) == 1
    assert snapshots[0]["cpu_avg"] == 10


# --- previous_snapshot -------------------------------------------------------

def test_previous_snapshot_picks_earlier_calendar_day():
    today = datetime.now(timezone.utc).date().isoformat()
    snaps = [
        {"timestamp": "2026-04-30T08:00:00+00:00", "cpu_avg": 10},
        {"timestamp": "2026-05-01T08:00:00+00:00", "cpu_avg": 12},
        {"timestamp": f"{today}T08:00:00+00:00", "cpu_avg": 15},
    ]
    prev = trends.previous_snapshot(snaps)
    assert prev["cpu_avg"] == 12  # most recent earlier-than-today


def test_previous_snapshot_falls_back_when_only_today():
    today = datetime.now(timezone.utc).date().isoformat()
    snaps = [
        {"timestamp": f"{today}T06:00:00+00:00", "cpu_avg": 10},
        {"timestamp": f"{today}T08:00:00+00:00", "cpu_avg": 15},
    ]
    prev = trends.previous_snapshot(snaps)
    assert prev["cpu_avg"] == 10  # second-most-recent fallback


def test_previous_snapshot_returns_none_for_single():
    snaps = [{"timestamp": "2026-05-01T08:00:00+00:00", "cpu_avg": 10}]
    assert trends.previous_snapshot(snaps) is None


def test_previous_snapshot_returns_none_for_empty():
    assert trends.previous_snapshot([]) is None


# --- compute_deltas ----------------------------------------------------------

def test_compute_deltas_basic():
    cur = {"cpu_avg": 30.0, "ram_avg": 65.0, "pending_total": 8,
           "pending_security": 3, "concerning_count": 1, "high_restart_count": 0,
           "network_avg": 100.0, "disks": {"disk:/": 50.0}}
    prev = {"cpu_avg": 25.0, "ram_avg": 60.0, "pending_total": 5,
            "pending_security": 2, "concerning_count": 0, "high_restart_count": 0,
            "network_avg": 90.0, "disks": {"disk:/": 48.0}}
    deltas = trends.compute_deltas(cur, prev)
    assert deltas["cpu_avg"]["delta_abs"] == 5.0
    assert deltas["cpu_avg"]["delta_pct"] == 20.0
    assert deltas["pending_security"]["delta_abs"] == 1
    assert deltas["disks"]["disk:/"]["delta_abs"] == 2.0


def test_compute_deltas_empty_when_no_previous():
    assert trends.compute_deltas({"cpu_avg": 1}, None) == {}
    assert trends.compute_deltas({"cpu_avg": 1}, {}) == {}


def test_compute_deltas_skips_missing_keys():
    cur = {"cpu_avg": 30.0}
    prev = {"ram_avg": 60.0}
    deltas = trends.compute_deltas(cur, prev)
    # Neither key has both sides → nothing to compare
    assert "cpu_avg" not in deltas
    assert "ram_avg" not in deltas


def test_compute_deltas_handles_zero_previous_safely():
    cur = {"pending_security": 3}
    prev = {"pending_security": 0}
    deltas = trends.compute_deltas(cur, prev)
    assert deltas["pending_security"]["delta_abs"] == 3
    # pct undefined when prev=0; we set None
    assert deltas["pending_security"]["delta_pct"] is None


# --- forecast_disk_fill ------------------------------------------------------

def _disk_snap(ts: str, pct: float) -> dict:
    return {"timestamp": ts, "disks": {"disk:/var/lib/docker": pct}}


def test_forecast_returns_none_when_too_few_points():
    snaps = [_disk_snap("2026-05-01T00:00:00+00:00", 50.0)]
    assert trends.forecast_disk_fill(snaps, "disk:/var/lib/docker") is None


def test_forecast_returns_none_for_flat_trend():
    snaps = [_disk_snap(f"2026-05-0{i}T00:00:00+00:00", 50.0) for i in range(1, 6)]
    assert trends.forecast_disk_fill(snaps, "disk:/var/lib/docker") is None


def test_forecast_returns_none_for_negative_trend():
    """Disk usage going down → no fill projection."""
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    snaps = [{"timestamp": (base + timedelta(days=i)).isoformat(),
              "disks": {"disk:/": 80.0 - i * 2}} for i in range(5)]
    assert trends.forecast_disk_fill(snaps, "disk:/") is None


def test_forecast_projects_growing_trend():
    """Linear growth from 50 to 70 over 4 days → projects ~6 more days to 100."""
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    snaps = [{"timestamp": (base + timedelta(days=i)).isoformat(),
              "disks": {"disk:/": 50.0 + i * 5}} for i in range(5)]
    result = trends.forecast_disk_fill(snaps, "disk:/")
    assert result is not None
    assert result["days_until_full"] > 0
    assert result["days_until_full"] < 30  # rough sanity
    assert result["current_pct"] == 70.0


# --- sparkline & series ------------------------------------------------------

def test_sparkline_uses_block_chars():
    out = trends.render_sparkline([1, 2, 3, 4, 5])
    assert all(c in trends.SPARKLINE_BLOCKS for c in out)
    assert len(out) == 5


def test_sparkline_flat_series():
    """All-equal values render as middle-block uniformly."""
    out = trends.render_sparkline([5, 5, 5, 5])
    assert len(out) == 4
    assert len(set(out)) == 1


def test_sparkline_empty():
    assert trends.render_sparkline([]) == ""


def test_sparkline_uses_full_range():
    """Min input → first block, max input → last block."""
    out = trends.render_sparkline([0, 100])
    assert out[0] == trends.SPARKLINE_BLOCKS[0]
    assert out[1] == trends.SPARKLINE_BLOCKS[-1]


def test_metric_series_pulls_scalar_key():
    snaps = [{"cpu_avg": 10}, {"cpu_avg": 20}, {"cpu_avg": None}, {"cpu_avg": 30}]
    assert trends.metric_series(snaps, "cpu_avg") == [10.0, 20.0, 30.0]


def test_metric_series_pulls_disk_key():
    snaps = [
        {"disks": {"disk:/": 50}},
        {"disks": {"disk:/": 55}},
        {"disks": {}},
        {"disks": {"disk:/": 60}},
    ]
    assert trends.metric_series(snaps, "disk:/") == [50.0, 55.0, 60.0]
