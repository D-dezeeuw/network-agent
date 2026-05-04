import raid


# --- Fixture mdstat samples taken straight from the real host -----------------

HEALTHY_MDSTAT = """\
Personalities : [raid1] [linear] [multipath] [raid0] [raid6] [raid5] [raid4] [raid10]\x20
md0 : active raid1 nvme0n1p1[2] nvme1n1p1[0]
      262080 blocks super 1.0 [2/2] [UU]

md1 : active raid1 nvme0n1p2[2] nvme1n1p2[0]
      33520640 blocks super 1.2 [2/2] [UU]

md3 : active raid1 nvme0n1p4[2] nvme1n1p4[0]
      465108288 blocks super 1.2 [2/2] [UU]
      bitmap: 2/4 pages [8KB], 65536KB chunk

md2 : active raid1 nvme0n1p3[2] nvme1n1p3[0]
      1046528 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""

REBUILDING_MDSTAT = """\
Personalities : [raid1]\x20
md0 : active raid1 nvme0n1p1[2] nvme1n1p1[0]
      262080 blocks super 1.0 [2/2] [UU]

md1 : active (auto-read-only) raid1 nvme0n1p2[2] nvme1n1p2[0]
      33520640 blocks super 1.2 [2/2] [UU]

md3 : active raid1 nvme0n1p4[2] nvme1n1p4[0]
      465108288 blocks super 1.2 [2/1] [U_]
      [=>...................]  recovery =  5.0% (23406848/465108288) finish=35.7min speed=205925K/sec
      bitmap: 4/4 pages [16KB], 65536KB chunk

md2 : active raid1 nvme0n1p3[2] nvme1n1p3[0]
      1046528 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""

DEGRADED_NO_REBUILD_MDSTAT = """\
Personalities : [raid1]\x20
md3 : active raid1 nvme1n1p4[0]
      465108288 blocks super 1.2 [2/1] [U_]
      bitmap: 2/4 pages [8KB], 65536KB chunk

unused devices: <none>
"""

EMPTY_MDSTAT = """\
Personalities : [raid1]\x20
unused devices: <none>
"""

WITH_FAILED_MEMBER_MDSTAT = """\
Personalities : [raid1]\x20
md0 : active raid1 nvme0n1p1[2] nvme1n1p1[0](F)
      262080 blocks super 1.0 [2/1] [_U]

unused devices: <none>
"""


# --- parse_mdstat ------------------------------------------------------------

def test_parse_healthy_returns_four_clean_arrays():
    arrays = raid.parse_mdstat(HEALTHY_MDSTAT)
    assert {a["name"] for a in arrays} == {"md0", "md1", "md2", "md3"}
    for a in arrays:
        assert a["state_pattern"] == "UU"
        assert a["degraded"] is False
        assert a["recovery"] is None
        assert a["slots_active"] == 2
        assert a["slots_total"] == 2


def test_parse_healthy_extracts_members_and_levels():
    arrays = raid.parse_mdstat(HEALTHY_MDSTAT)
    md0 = next(a for a in arrays if a["name"] == "md0")
    assert md0["level"] == "raid1"
    assert {m["device"] for m in md0["members"]} == {"nvme0n1p1", "nvme1n1p1"}


def test_parse_rebuilding_extracts_progress():
    arrays = raid.parse_mdstat(REBUILDING_MDSTAT)
    md3 = next(a for a in arrays if a["name"] == "md3")
    assert md3["state_pattern"] == "U_"
    assert md3["degraded"] is True
    assert md3["recovery"] is not None
    assert md3["recovery"]["action"] == "recovery"
    assert md3["recovery"]["percent"] == 5.0
    assert md3["recovery"]["finish_min"] == 35.7
    assert md3["recovery"]["speed_kbps"] == 205925


def test_parse_handles_auto_read_only_swap():
    """md1 with (auto-read-only) is normal — must still parse cleanly."""
    arrays = raid.parse_mdstat(REBUILDING_MDSTAT)
    md1 = next(a for a in arrays if a["name"] == "md1")
    assert md1["auto_read_only"] is True
    assert md1["level"] == "raid1"
    assert md1["degraded"] is False


def test_parse_degraded_no_rebuild_member_count():
    arrays = raid.parse_mdstat(DEGRADED_NO_REBUILD_MDSTAT)
    assert len(arrays) == 1
    md3 = arrays[0]
    assert md3["state_pattern"] == "U_"
    assert md3["degraded"] is True
    assert md3["recovery"] is None
    assert len(md3["members"]) == 1


def test_parse_empty_returns_no_arrays():
    assert raid.parse_mdstat(EMPTY_MDSTAT) == []


def test_parse_failed_member_flag():
    arrays = raid.parse_mdstat(WITH_FAILED_MEMBER_MDSTAT)
    md0 = arrays[0]
    failed = [m for m in md0["members"] if m["failed"]]
    assert len(failed) == 1
    assert failed[0]["device"] == "nvme1n1p1"


# --- _summarize / severity ---------------------------------------------------

def test_summarize_healthy():
    arrays = raid.parse_mdstat(HEALTHY_MDSTAT)
    severity, summary = raid._summarize(arrays)
    assert severity == "healthy"
    assert "4 arrays" in summary


def test_summarize_recovering_takes_priority_over_degraded():
    """An array under rebuild is degraded BUT severity should be `recovering`,
    not `degraded` — the rebuild is self-resolving."""
    arrays = raid.parse_mdstat(REBUILDING_MDSTAT)
    severity, summary = raid._summarize(arrays)
    assert severity == "recovering"
    assert "md3" in summary
    assert "5.0%" in summary


def test_summarize_degraded_when_no_rebuild():
    """The escalation case — disk gone, nothing happening."""
    arrays = raid.parse_mdstat(DEGRADED_NO_REBUILD_MDSTAT)
    severity, summary = raid._summarize(arrays)
    assert severity == "degraded"
    assert "md3" in summary
    assert "no rebuild" in summary.lower()


def test_summarize_empty_arrays_is_healthy():
    severity, summary = raid._summarize([])
    assert severity == "healthy"
    assert "no arrays" in summary


# --- get_status (file-backed) ------------------------------------------------

def test_get_status_when_mdstat_missing(tmp_path):
    out = raid.get_status(str(tmp_path / "missing"))
    assert out["enabled"] is False
    assert "not found" in out["reason"]


def test_get_status_full_record_for_healthy_host(tmp_path):
    p = tmp_path / "mdstat"
    p.write_text(HEALTHY_MDSTAT)
    out = raid.get_status(str(p))
    assert out["enabled"] is True
    assert out["array_count"] == 4
    assert out["degraded_count"] == 0
    assert out["any_degraded"] is False
    assert out["any_recovering"] is False
    assert out["severity"] == "healthy"


def test_get_status_full_record_during_rebuild(tmp_path):
    p = tmp_path / "mdstat"
    p.write_text(REBUILDING_MDSTAT)
    out = raid.get_status(str(p))
    assert out["enabled"] is True
    assert out["any_degraded"] is True
    assert out["any_recovering"] is True
    assert out["severity"] == "recovering"
    assert out["degraded_count"] == 1


def test_get_status_full_record_when_degraded_with_no_rebuild(tmp_path):
    p = tmp_path / "mdstat"
    p.write_text(DEGRADED_NO_REBUILD_MDSTAT)
    out = raid.get_status(str(p))
    assert out["severity"] == "degraded"
    assert out["any_degraded"] is True
    assert out["any_recovering"] is False
