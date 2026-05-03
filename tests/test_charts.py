"""Smoke tests for chart rendering. We don't validate the image content —
just that we get non-empty PNG bytes back. matplotlib's pixel output isn't
worth pinning, but the PNG signature and a sane minimum size are."""

import pytest

charts = pytest.importorskip("charts", reason="charts requires matplotlib")


def _is_png(blob: bytes) -> bool:
    return isinstance(blob, (bytes, bytearray)) and blob[:8] == charts.PNG_SIGNATURE


def test_sparkline_returns_png_bytes():
    out = charts.render_sparkline([1, 2, 3, 4, 5], title="cpu")
    assert _is_png(out)
    assert len(out) > 200  # any reasonable PNG is at least this big


def test_sparkline_handles_empty_series():
    """Empty input still produces a valid 'no data' chart, not an exception."""
    out = charts.render_sparkline([], title="cpu")
    assert _is_png(out)


def test_sparkline_handles_flat_series():
    out = charts.render_sparkline([5, 5, 5, 5], title="ram")
    assert _is_png(out)


def test_sparkline_handles_long_series():
    out = charts.render_sparkline(list(range(100)))
    assert _is_png(out)


def test_status_grid_with_containers_and_disks():
    containers = [
        {"name": "ok-svc", "status": "running", "health": "healthy",
         "restart_count": 0, "exit_code": None},
        {"name": "broken", "status": "exited", "health": None,
         "restart_count": 5, "exit_code": 1},
        {"name": "loopy", "status": "restarting", "health": None,
         "restart_count": 12, "exit_code": 0},
    ]
    disks = {"disk:/": 45.0, "disk:/var/lib/docker": 88.0}
    out = charts.render_status_grid(containers, disks)
    assert _is_png(out)


def test_status_grid_handles_empty_inputs():
    out = charts.render_status_grid([], {})
    assert _is_png(out)


def test_status_grid_skips_non_numeric_disk_usage():
    """A garbled disk entry shouldn't crash the renderer."""
    disks = {"disk:/": "not-a-number", "disk:/var": 50.0}
    out = charts.render_status_grid([], disks)
    assert _is_png(out)


def test_is_png_helper():
    assert charts.is_png(charts.PNG_SIGNATURE + b"rest of the file")
    assert not charts.is_png(b"")
    assert not charts.is_png(b"not a png")


def test_container_color_map_critical():
    assert charts._container_color({"status": "running", "health": "unhealthy"}) == "#ff6b6b"
    assert charts._container_color({"status": "dead"}) == "#ff6b6b"
    assert charts._container_color({"status": "restarting"}) == "#ff6b6b"
    assert charts._container_color({"status": "exited", "exit_code": 1}) == "#ff6b6b"


def test_container_color_map_ok_and_neutral():
    assert charts._container_color({"status": "running"}) == "#7ed957"
    assert charts._container_color({"status": "exited", "exit_code": 0}) == "#cccccc"
    assert charts._container_color({"status": "paused"}) == "#f5d77a"


def test_disk_color_thresholds():
    assert charts._disk_color(95) == "#ff6b6b"
    assert charts._disk_color(80) == "#f5d77a"
    assert charts._disk_color(50) == "#7ed957"
