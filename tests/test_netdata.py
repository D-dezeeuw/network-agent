from netdata import _decode_mount, summarize_chart


def test_summarize_chart_empty():
    assert summarize_chart({}) == {}
    assert summarize_chart({"data": []}) == {}


def test_summarize_chart_skips_nones():
    data = {"data": [[0, 10.0], [1, None], [2, 20.0], [3, 30.0]]}
    assert summarize_chart(data) == {"min": 10.0, "max": 30.0, "avg": 20.0}


def test_summarize_chart_rounds():
    data = {"data": [[0, 1.111], [1, 2.222], [2, 3.333]]}
    result = summarize_chart(data)
    assert result["min"] == 1.11
    assert result["max"] == 3.33
    assert result["avg"] == 2.22


def test_summarize_chart_all_none():
    data = {"data": [[0, None], [1, None]]}
    assert summarize_chart(data) == {}


def test_decode_mount_root():
    assert _decode_mount("_") == "/"


def test_decode_mount_single_level():
    assert _decode_mount("_boot") == "/boot"


def test_decode_mount_nested():
    assert _decode_mount("_var_lib_docker") == "/var/lib/docker"
