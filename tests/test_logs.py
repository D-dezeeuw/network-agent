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
