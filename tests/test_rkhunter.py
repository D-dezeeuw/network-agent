import os

import rkhunter


SAMPLE_LOG = """\
[ Rootkit Hunter version 1.4.6 ]

Checking system commands...
Performing 'strings' command checks
    Checking 'strings' command                               [ OK ]
Warning: The command '/usr/bin/foo' has changed since last run
Warning: Suspicious file types found in /dev:
         /dev/.something
Warning: Hidden directory found: /etc/.hidden
Information: Skipping unhash check
Warning: Found enabled xinetd service: chargen
Checking system configuration files...
    All files OK.
"""


def test_get_status_when_log_missing(tmp_path):
    missing = tmp_path / "no-such-log.log"
    out = rkhunter.get_status(str(missing))
    assert out["enabled"] is False
    assert "not found" in out["reason"]
    assert out["log_path"] == str(missing)


def test_get_status_counts_warnings(tmp_path):
    log = tmp_path / "rkhunter-combined.log"
    log.write_text(SAMPLE_LOG)
    out = rkhunter.get_status(str(log))
    assert out["enabled"] is True
    assert out["total_warnings"] == 4
    assert any("Hidden directory" in w for w in out["recent_warnings"])


def test_get_status_keeps_only_tail(tmp_path):
    """Recent-warnings deque caps at RECENT_WARNINGS_TAIL."""
    log = tmp_path / "rkhunter-combined.log"
    lines = [f"Warning: entry-{i}" for i in range(50)]
    log.write_text("\n".join(lines))
    out = rkhunter.get_status(str(log))
    assert out["total_warnings"] == 50
    assert len(out["recent_warnings"]) == rkhunter.RECENT_WARNINGS_TAIL
    # tail preserves the LAST entries, not the first
    assert "entry-49" in out["recent_warnings"][-1]
    assert "entry-40" in out["recent_warnings"][0]


def test_get_status_handles_empty_log(tmp_path):
    log = tmp_path / "rkhunter-combined.log"
    log.write_text("")
    out = rkhunter.get_status(str(log))
    assert out["enabled"] is True
    assert out["total_warnings"] == 0
    assert out["recent_warnings"] == []
    assert out["size_bytes"] == 0


def test_get_status_reports_size_and_mtime(tmp_path):
    log = tmp_path / "rkhunter-combined.log"
    log.write_text(SAMPLE_LOG)
    out = rkhunter.get_status(str(log))
    assert out["size_bytes"] == os.path.getsize(log)
    assert out["last_modified"].endswith("+00:00")  # ISO with UTC tz


def test_get_status_handles_non_utf8_bytes(tmp_path):
    """Real log files can contain odd bytes — replace, don't crash."""
    log = tmp_path / "rkhunter-combined.log"
    log.write_bytes(b"Warning: some \xff\xfe bytes\nWarning: another\n")
    out = rkhunter.get_status(str(log))
    assert out["enabled"] is True
    assert out["total_warnings"] == 2


def test_get_status_uses_env_var_when_no_arg(monkeypatch, tmp_path):
    log = tmp_path / "rkhunter-combined.log"
    log.write_text("Warning: env-var-routing-test\n")
    monkeypatch.setenv("RKHUNTER_LOG_PATH", str(log))
    out = rkhunter.get_status()
    assert out["enabled"] is True
    assert out["total_warnings"] == 1


def test_get_status_picks_lines_containing_warning_anywhere(tmp_path):
    """The user's grep -c "Warning" matches anywhere in the line — match the same shape."""
    log = tmp_path / "rkhunter-combined.log"
    log.write_text(
        "    Warning: leading whitespace\n"
        "info: word Warning embedded mid-line\n"
        "no match here\n"
        "Warning: at the start\n"
    )
    out = rkhunter.get_status(str(log))
    assert out["total_warnings"] == 3
