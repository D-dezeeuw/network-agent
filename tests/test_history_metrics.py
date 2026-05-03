import history_metrics


def _record(timestamp: str, **overrides) -> dict:
    base = {
        "timestamp": timestamp,
        "auth": {},
        "fail2ban": {},
        "metrics": {},
        "derived": {},
        "system_health": {},
    }
    base.update(overrides)
    return base


def test_known_metrics_alphabetical():
    metrics = history_metrics.known_metrics()
    assert metrics == sorted(metrics)
    assert "fail2ban" in metrics
    assert "probes" in metrics


def test_metric_info_unknown_returns_none():
    assert history_metrics.metric_info("nonsense") is None


def test_metric_info_known_returns_dict():
    info = history_metrics.metric_info("fail2ban")
    assert info["path"] == "fail2ban.bans_24h"
    assert "bans" in info["label"].lower()


def test_metric_info_normalizes_case_and_whitespace():
    assert history_metrics.metric_info("  Fail2Ban  ")["path"] == "fail2ban.bans_24h"


def test_series_for_metric_extracts_present_values():
    records = [
        _record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": 5}),
        _record("2026-05-02T08:00:00Z", fail2ban={"bans_24h": 12}),
        _record("2026-05-03T08:00:00Z", fail2ban={"bans_24h": 0}),
    ]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 3
    assert [v for _, v in points] == [5.0, 12.0, 0.0]


def test_series_skips_records_missing_the_value():
    """Records without the metric key shouldn't be coerced to zero — they
    just don't contribute. 'No data' ≠ 'zero'."""
    records = [
        _record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": 5}),
        _record("2026-05-02T08:00:00Z", fail2ban={}),   # missing
        _record("2026-05-03T08:00:00Z", fail2ban={"bans_24h": 7}),
    ]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 2
    assert [v for _, v in points] == [5.0, 7.0]


def test_series_skips_non_numeric_values():
    records = [
        _record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": "thirteen"}),
        _record("2026-05-02T08:00:00Z", fail2ban={"bans_24h": 7}),
    ]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 1


def test_series_skips_bool_values():
    """`True` is technically `int` in Python — but it's never meaningful for
    a count metric, and silently treating `True == 1` would be misleading."""
    records = [
        _record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": True}),
        _record("2026-05-02T08:00:00Z", fail2ban={"bans_24h": 5}),
    ]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 1
    assert points[0][1] == 5.0


def test_series_skips_records_with_bad_timestamp():
    records = [
        _record("not-a-timestamp", fail2ban={"bans_24h": 5}),
        _record("2026-05-02T08:00:00Z", fail2ban={"bans_24h": 7}),
    ]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 1


def test_series_for_unknown_metric_returns_empty():
    records = [_record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": 5})]
    assert history_metrics.series_for_metric(records, "nonsense") == []


def test_series_extracts_nested_metrics_path():
    """`/history cpu` reads metrics.cpu.avg — verify the dotted path resolves."""
    records = [
        _record("2026-05-01T08:00:00Z", metrics={"cpu": {"avg": 22.5}}),
        _record("2026-05-02T08:00:00Z", metrics={"cpu": {"avg": 31.2}}),
    ]
    points = history_metrics.series_for_metric(records, "cpu")
    assert [round(v, 1) for _, v in points] == [22.5, 31.2]


def test_series_handles_z_suffix_timestamp():
    records = [_record("2026-05-01T08:00:00Z", fail2ban={"bans_24h": 5})]
    points = history_metrics.series_for_metric(records, "fail2ban")
    assert len(points) == 1
    # Parsed UTC
    assert points[0][0].tzinfo is not None
