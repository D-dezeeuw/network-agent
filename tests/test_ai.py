from ai import SECTION_MARKERS, split_report


def test_split_report_four_sections():
    report = (
        "##STATUS##\nAll good\n"
        "##SECURITY##\nNo deltas\n"
        "##HEALTH##\nReboot pending\n"
        "##METRICS##\nCPU 12%"
    )
    parts = split_report(report)
    assert len(parts) == 4
    assert parts[0] == "All good"
    assert parts[1] == "No deltas"
    assert parts[2] == "Reboot pending"
    assert parts[3] == "CPU 12%"


def test_split_report_preserves_order_when_markers_arrive_out_of_order():
    """Even if Claude emits sections in wrong order, we surface them in document order."""
    report = (
        "##METRICS##\nm-body\n"
        "##STATUS##\ns-body\n"
        "##SECURITY##\nsec-body\n"
        "##HEALTH##\nh-body"
    )
    parts = split_report(report)
    assert parts == ["m-body", "s-body", "sec-body", "h-body"]


def test_split_report_falls_back_to_single_part_when_no_markers():
    report = "Just a plain string with no markers"
    parts = split_report(report)
    assert parts == [report]


def test_split_report_skips_empty_sections():
    report = "##STATUS##\nfilled\n##SECURITY##\n##HEALTH##\nalso filled\n##METRICS##\n"
    parts = split_report(report)
    assert "filled" in parts
    assert "also filled" in parts
    assert "" not in parts


def test_split_report_handles_empty_input():
    assert split_report("") == ["(empty report)"]
    assert split_report(None) == ["(empty report)"]


def test_section_markers_constant_is_complete():
    """Smoke test: the 4 documented sections are all present."""
    expected = {"##STATUS##", "##SECURITY##", "##HEALTH##", "##METRICS##"}
    assert set(SECTION_MARKERS) == expected
