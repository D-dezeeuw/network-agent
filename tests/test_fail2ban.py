import sqlite3
import time

import fail2ban


def _build_db(tmp_path, rows):
    """Create a fresh fail2ban-shaped sqlite db and seed the bans table."""
    db = tmp_path / "fail2ban.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE bans (ip TEXT, jail TEXT NOT NULL, "
        "timeofban INTEGER NOT NULL, data TEXT)"
    )
    conn.executemany(
        "INSERT INTO bans (ip, jail, timeofban, data) VALUES (?, ?, ?, ?)",
        [(ip, jail, ts, "{}") for ip, jail, ts in rows],
    )
    conn.commit()
    conn.close()
    return str(db)


def test_get_status_returns_disabled_when_db_missing(tmp_path):
    result = fail2ban.get_status(db_path=str(tmp_path / "nope.sqlite3"))
    assert result["enabled"] is False
    assert "not found" in result["reason"]


def test_get_status_empty_db_returns_zero_counts(tmp_path):
    db = _build_db(tmp_path, [])
    result = fail2ban.get_status(db_path=db, now_ts=1_700_000_000.0)
    assert result["enabled"] is True
    assert result["bans_24h"] == 0
    assert result["bans_7d"] == 0
    assert result["top_banned_ips_24h"] == []
    assert result["top_jails_24h"] == []
    assert result["recent_sample"] == []


def test_get_status_counts_bans_in_24h_window(tmp_path):
    now = 1_700_000_000.0
    rows = [
        ("1.2.3.4", "sshd", int(now - 3600)),       # 1h ago — in 24h
        ("1.2.3.4", "sshd", int(now - 7200)),       # 2h ago — in 24h
        ("5.6.7.8", "sshd", int(now - 12 * 3600)),  # 12h ago — in 24h
        ("9.9.9.9", "nginx", int(now - 48 * 3600)),  # 2d ago — in 7d only
        ("4.4.4.4", "sshd", int(now - 30 * 24 * 3600)),  # 30d — out of 7d
    ]
    db = _build_db(tmp_path, rows)
    result = fail2ban.get_status(db_path=db, now_ts=now)

    assert result["enabled"] is True
    assert result["bans_24h"] == 3
    assert result["bans_7d"] == 4
    # Top IP in 24h window is 1.2.3.4 (banned twice)
    assert result["top_banned_ips_24h"][0] == ("1.2.3.4", 2)
    # Top jail in 24h is sshd (3 events)
    assert result["top_jails_24h"][0] == ("sshd", 3)


def test_get_status_recent_sample_is_newest_first(tmp_path):
    now = 1_700_000_000.0
    rows = [
        ("1.1.1.1", "sshd", int(now - 100)),
        ("2.2.2.2", "sshd", int(now - 200)),
        ("3.3.3.3", "sshd", int(now - 300)),
    ]
    db = _build_db(tmp_path, rows)
    result = fail2ban.get_status(db_path=db, now_ts=now)

    sample_ips = [s["ip"] for s in result["recent_sample"]]
    assert sample_ips == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]


def test_get_status_handles_corrupt_db(tmp_path):
    db = tmp_path / "garbage.sqlite3"
    db.write_bytes(b"not a sqlite database")
    result = fail2ban.get_status(db_path=str(db))
    assert result["enabled"] is False
    assert "sqlite error" in result["reason"] or "unexpected error" in result["reason"]


def test_get_status_handles_missing_table(tmp_path):
    db = tmp_path / "wrong.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    result = fail2ban.get_status(db_path=str(db))
    assert result["enabled"] is False
    assert "sqlite error" in result["reason"]


def test_open_readonly_actually_blocks_writes(tmp_path):
    """Defense check: even if fail2ban code somehow tried to write, the URI
    flag prevents it."""
    db = _build_db(tmp_path, [("1.1.1.1", "sshd", int(time.time()))])
    conn = fail2ban._open_readonly(db)
    try:
        import pytest
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO bans (ip, jail, timeofban, data) VALUES "
                         "('9.9.9.9', 'sshd', 0, '{}')")
            conn.commit()
    finally:
        conn.close()
