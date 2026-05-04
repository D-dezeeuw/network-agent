"""Software-RAID (mdadm) status reader.

Parses /proc/mdstat from the host to surface array health: per-array
state pattern ([UU]/[U_]/[__]), member list, and any in-progress
rebuild/resync activity. Read-only — we never touch mdadm.

Returns `enabled: False` when /proc/mdstat is missing (no kernel md
support) so downstream consumers can skip silently.
"""

import os
import re

from config import HOST_PREFIX

DEFAULT_MDSTAT_PATH = os.path.join(HOST_PREFIX, "proc/mdstat")

# Header line: `md0 : active raid1 nvme0n1p1[2] nvme1n1p1[0]`
_HEADER_RE = re.compile(r"^(md\d+)\s*:\s*(.+)$")
# Status line: `      262080 blocks super 1.0 [2/2] [UU]`
_BLOCKS_RE = re.compile(r"(\d+)\s+blocks")
_SLOTS_RE = re.compile(r"\[(\d+)/(\d+)\]")
# Final bracketed token on the status line is the up/down map: [UU], [U_], etc.
_STATE_RE = re.compile(r"\[([U_]+)\]\s*$")
# Recovery / resync / reshape line, with optional finish + speed fields.
_RECOVERY_RE = re.compile(
    r"(?P<action>recovery|resync|reshape|check)\s*=\s*(?P<pct>[\d.]+)%"
    r"(?:.*?finish=(?P<finish>[\d.]+)min)?"
    r"(?:.*?speed=(?P<speed>\d+)K/sec)?"
)
# Member token: `nvme0n1p1[2]` or `nvme0n1p1[0](F)` (failed) / `(S)` (spare).
_MEMBER_RE = re.compile(r"^(\S+?)\[(\d+)\](\([FS]\))?$")


def _parse_array_block(lines: list[str]) -> dict | None:
    """Parse a single array's lines (header + status + optional progress)."""
    if not lines:
        return None
    header = _HEADER_RE.match(lines[0])
    if not header:
        return None
    name = header.group(1)
    rest_tokens = header.group(2).split()

    # Header tokens split into: status word(s) + level + member list.
    # Status can include parenthesized states like "(auto-read-only)".
    level = None
    state_words: list[str] = []
    members: list[dict] = []
    for tok in rest_tokens:
        member_match = _MEMBER_RE.match(tok)
        if member_match:
            members.append({
                "device": member_match.group(1),
                "role": int(member_match.group(2)),
                "failed": member_match.group(3) == "(F)",
                "spare": member_match.group(3) == "(S)",
            })
        elif tok.startswith("raid") or tok in ("linear", "multipath"):
            level = tok
        else:
            state_words.append(tok)

    # Status line (line 2): blocks count, super version, slot ratio, state map.
    status_line = lines[1] if len(lines) > 1 else ""
    blocks_match = _BLOCKS_RE.search(status_line)
    slots_match = _SLOTS_RE.search(status_line)
    state_match = _STATE_RE.search(status_line)

    blocks = int(blocks_match.group(1)) if blocks_match else None
    slots_total = int(slots_match.group(1)) if slots_match else None
    slots_active = int(slots_match.group(2)) if slots_match else None
    state_pattern = state_match.group(1) if state_match else None

    # Subsequent lines may carry recovery progress — first match wins.
    recovery = None
    for line in lines[2:]:
        m = _RECOVERY_RE.search(line)
        if m:
            recovery = {
                "action": m.group("action"),
                "percent": float(m.group("pct")),
                "finish_min": float(m.group("finish")) if m.group("finish") else None,
                "speed_kbps": int(m.group("speed")) if m.group("speed") else None,
            }
            break

    degraded = bool(state_pattern and "_" in state_pattern)

    return {
        "name": name,
        "level": level,
        "active": "active" in state_words,
        "auto_read_only": any("(auto-read-only)" in w for w in state_words),
        "members": members,
        "blocks": blocks,
        "slots_total": slots_total,
        "slots_active": slots_active,
        "state_pattern": state_pattern,
        "degraded": degraded,
        "recovery": recovery,
    }


def parse_mdstat(content: str) -> list[dict]:
    """Split mdstat text into per-array blocks and parse each."""
    arrays: list[dict] = []
    current: list[str] = []
    for line in content.splitlines():
        # Section break on empty line — flush current block.
        if not line.strip():
            if current:
                parsed = _parse_array_block(current)
                if parsed:
                    arrays.append(parsed)
                current = []
            continue
        # Skip header / footer noise we never want to treat as array blocks.
        if line.startswith("Personalities") or line.startswith("unused devices"):
            if current:
                parsed = _parse_array_block(current)
                if parsed:
                    arrays.append(parsed)
                current = []
            continue
        current.append(line)
    # Flush trailing block (file may not end with blank line).
    if current:
        parsed = _parse_array_block(current)
        if parsed:
            arrays.append(parsed)
    return arrays


def _summarize(arrays: list[dict]) -> tuple[str, str]:
    """Return (severity, human_summary) for a parsed array list.

    Severity levels:
      healthy    — every array [UU...] with no rebuild in progress
      recovering — at least one array has a recovery/resync line
      degraded   — at least one array shows [U_] or worse with NO rebuild
                   in progress (a disk is gone and nothing is being rebuilt)
    """
    if not arrays:
        return "healthy", "no arrays configured"

    degraded_now = [a for a in arrays if a["degraded"]]
    recovering = [a for a in arrays if a.get("recovery")]

    if not degraded_now and not recovering:
        return "healthy", f"all {len(arrays)} arrays healthy"

    if recovering:
        worst = min((a["recovery"]["percent"] for a in recovering),
                    default=0.0)
        names = ", ".join(a["name"] for a in recovering)
        return ("recovering",
                f"{len(recovering)} array(s) rebuilding "
                f"({names}, {worst:.1f}% of slowest)")

    # Degraded but no rebuild → human attention required.
    names = ", ".join(a["name"] for a in degraded_now)
    return "degraded", f"{len(degraded_now)} array(s) degraded with no rebuild in progress ({names})"


def get_status(mdstat_path: str | None = None) -> dict:
    """Read /proc/mdstat and return a structured summary."""
    path = mdstat_path or DEFAULT_MDSTAT_PATH
    if not os.path.exists(path):
        return {
            "enabled": False,
            "reason": f"mdstat not found at {path}",
        }
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return {
            "enabled": False,
            "reason": f"read failed: {e}",
        }

    arrays = parse_mdstat(content)
    severity, summary = _summarize(arrays)
    any_degraded = any(a["degraded"] for a in arrays)
    any_recovering = any(a.get("recovery") for a in arrays)
    return {
        "enabled": True,
        "arrays": arrays,
        "array_count": len(arrays),
        "degraded_count": sum(1 for a in arrays if a["degraded"]),
        "any_degraded": any_degraded,
        "any_recovering": any_recovering,
        "severity": severity,
        "summary": summary,
    }
