from system_health import _filter_notable_kernel, _is_concerning_container, _parse_apt_output


APT_SAMPLE = """\
Listing... Done
ca-certificates/stable-security 20230311+deb12u8 all [upgradable from: 20230311+deb12u7]
libssl3/stable-security 3.0.14-1~deb12u2 amd64 [upgradable from: 3.0.13-1~deb12u1]
curl/stable 7.88.1-10+deb12u8 amd64 [upgradable from: 7.88.1-10+deb12u7]
N: ignored line without slash
"""


def test_parse_apt_output_counts_total_and_security():
    result = _parse_apt_output(APT_SAMPLE)
    assert result["total"] == 3
    assert result["security"] == 2
    assert "ca-certificates" in result["security_packages"]
    assert "libssl3" in result["security_packages"]
    assert "curl" not in result["security_packages"]


def test_parse_apt_output_empty():
    result = _parse_apt_output("Listing... Done\n")
    assert result["total"] == 0
    assert result["security"] == 0
    assert result["security_packages"] == []


def test_parse_apt_output_ignores_malformed_lines():
    junk = "random line\nanother [upgradable] without slash\n"
    result = _parse_apt_output(junk)
    assert result["total"] == 0


def test_filter_notable_kernel_picks_oom():
    lines = [
        "kernel: cgroup-out-of-memory: Killed process 1234 (myproc)",
        "kernel: random unrelated line",
    ]
    notable = _filter_notable_kernel(lines)
    assert len(notable) == 1
    assert "Killed process" in notable[0]


def test_filter_notable_kernel_picks_io_error():
    lines = [
        "kernel: ata1.00: I/O error, dev sda, sector 12345",
        "kernel: usb 1-1: new high-speed USB device number 2",
    ]
    notable = _filter_notable_kernel(lines)
    assert len(notable) == 1
    assert "I/O error" in notable[0]


def test_filter_notable_kernel_picks_segfault_and_panic():
    lines = [
        "kernel: myproc[1234]: segfault at 0 ip 00007f...",
        "kernel: Kernel panic - not syncing: ...",
        "kernel: scheduling while atomic",
    ]
    notable = _filter_notable_kernel(lines)
    assert len(notable) == 2


def test_filter_notable_kernel_no_matches():
    lines = ["kernel: routine info message", "kernel: another normal line"]
    assert _filter_notable_kernel(lines) == []


def test_concerning_unhealthy_health_check():
    assert _is_concerning_container({"Status": "running"}, "unhealthy") is True


def test_concerning_dead_status():
    assert _is_concerning_container({"Status": "dead"}, None) is True


def test_concerning_restarting():
    assert _is_concerning_container({"Status": "restarting"}, None) is True


def test_concerning_exited_nonzero_is_failure():
    assert _is_concerning_container({"Status": "exited", "ExitCode": 137}, None) is True


def test_clean_exited_is_not_concerning():
    """Exit code 0 = clean shutdown, e.g. one-shot task completed."""
    assert _is_concerning_container({"Status": "exited", "ExitCode": 0}, None) is False


def test_running_with_starting_health_is_not_concerning():
    assert _is_concerning_container({"Status": "running"}, "starting") is False


def test_running_with_no_healthcheck_is_not_concerning():
    assert _is_concerning_container({"Status": "running"}, None) is False


def test_paused_is_not_concerning():
    assert _is_concerning_container({"Status": "paused"}, None) is False
