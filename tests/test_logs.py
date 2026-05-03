from unittest.mock import patch

import logs


SAMPLE_LINES = [
    "May  1 10:00:00 host sshd[1]: Failed password for root from 192.168.1.10 port 22",
    "May  1 10:00:01 host sshd[2]: Failed password for root from 192.168.1.10 port 22",
    "May  1 10:00:02 host sshd[3]: Invalid user admin from 10.0.0.5 port 22",
    "May  1 10:01:00 host sshd[4]: Accepted publickey for user from 192.168.1.20 port 22",
    "May  1 10:02:00 host sshd[5]: Some unrelated log line",
]


def test_get_auth_log_summary_counts():
    with patch.object(logs, "_read_via_journalctl", return_value=SAMPLE_LINES):
        result = logs.get_auth_log_summary(24)

    assert result["failed_attempts"] == 3
    assert result["successful_logins"] == 1
    assert ("192.168.1.10", 2) in result["top_attacker_ips"]
    assert ("10.0.0.5", 1) in result["top_attacker_ips"]
    assert len(result["raw_sample"]) == 3


def test_get_auth_log_summary_journalctl_failure_falls_back():
    with patch.object(logs, "_read_via_journalctl", side_effect=RuntimeError("boom")), \
         patch.object(logs, "_read_via_file", return_value=SAMPLE_LINES):
        result = logs.get_auth_log_summary(24)

    assert result["failed_attempts"] == 3
    assert result["successful_logins"] == 1


def test_get_auth_log_summary_empty():
    with patch.object(logs, "_read_via_journalctl", return_value=[]):
        result = logs.get_auth_log_summary(24)

    assert result["failed_attempts"] == 0
    assert result["successful_logins"] == 0
    assert result["top_attacker_ips"] == []
    assert result["raw_sample"] == []
    assert result["port_probes"] == 0
    assert result["top_probe_ips"] == []


# --- pre-auth probe detection ------------------------------------------------

PROBE_LINES = [
    "May  1 12:00:00 host sshd[10]: Connection from 203.0.113.5 port 51234",
    "May  1 12:00:01 host sshd[11]: Did not receive identification string from 203.0.113.5 port 51235",
    "May  1 12:00:02 host sshd[12]: Connection closed by 198.51.100.7 port 22 [preauth]",
    "May  1 12:00:03 host sshd[13]: Connection reset by 198.51.100.7 port 22 [preauth]",
]

NON_PROBE_LINES = [
    # Connection closed AFTER auth — not a probe, normal session teardown.
    "May  1 12:05:00 host sshd[20]: Connection closed by user 192.168.1.50 port 22",
    # Generic noise.
    "May  1 12:05:01 host sshd[21]: pam_unix(sshd:session): session opened",
]


def test_is_probe_line_recognizes_pre_auth_signals():
    for line in PROBE_LINES:
        assert logs._is_probe_line(line) is True, f"should be probe: {line!r}"


def test_is_probe_line_skips_post_auth_disconnects():
    for line in NON_PROBE_LINES:
        assert logs._is_probe_line(line) is False, f"should NOT be probe: {line!r}"


def test_get_auth_log_summary_counts_probes():
    with patch.object(logs, "_read_via_journalctl", return_value=PROBE_LINES + NON_PROBE_LINES):
        result = logs.get_auth_log_summary(24)
    assert result["port_probes"] == 4
    # Two distinct probe IPs across the 4 probe lines
    ip_counts = dict(result["top_probe_ips"])
    assert ip_counts.get("203.0.113.5") == 2
    assert ip_counts.get("198.51.100.7") == 2


def test_failed_and_probe_counts_are_independent():
    """A line that's both a Failed password and matches probe pattern shouldn't
    be double-counted (in practice they don't co-occur but the two pipelines
    are independent — so failed=1 and probes=0 for a pure failure)."""
    lines = ["May  1 10:00:00 host sshd[1]: Failed password for root from 192.168.1.10 port 22"]
    with patch.object(logs, "_read_via_journalctl", return_value=lines):
        result = logs.get_auth_log_summary(24)
    assert result["failed_attempts"] == 1
    assert result["port_probes"] == 0


def test_extract_ips_pulls_first_ipv4():
    lines = [
        "May  1 10:00 sshd: from 1.2.3.4 to 5.6.7.8",
        "May  1 10:01 sshd: no ip here",
        "May  1 10:02 sshd: 9.10.11.12",
    ]
    assert logs._extract_ips(lines) == ["1.2.3.4", "9.10.11.12"]
