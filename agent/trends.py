"""Snapshot persistence and trend analysis.

Each digest cycle saves a small JSON snapshot of the headline numerics
(CPU/RAM/network averages, disk usage per mount, container counts,
pending updates). Subsequent runs diff against an earlier snapshot to
annotate the digest with deltas and to forecast disk fill.

Snapshots live at /state/snapshots/<utc-timestamp>.json. We keep the
most recent KEEP_SNAPSHOTS files and prune older.
"""

import json
import os
from datetime import datetime, timezone
from glob import glob

from config import STATE_DIR

SNAPSHOTS_DIR = os.path.join(STATE_DIR, "snapshots")
KEEP_SNAPSHOTS = 30
SPARKLINE_BLOCKS = "▁▂▃▄▅▆▇█"


# --- timestamp helpers -------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


# --- snapshot extraction & I/O ----------------------------------------------

def _safe_get(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def extract_snapshot(metrics: dict, health: dict) -> dict:
    """Pull the small set of numerics worth diffing day-over-day."""
    snap = {
        "timestamp": _now().isoformat(),
        "cpu_avg": _safe_get(metrics, "cpu", "avg"),
        "ram_avg": _safe_get(metrics, "ram", "avg"),
        "network_avg": _safe_get(metrics, "network", "avg"),
        "pending_total": _safe_get(health, "pending_updates", "total", default=0),
        "pending_security": _safe_get(health, "pending_updates", "security", default=0),
        "concerning_count": len(_safe_get(health, "docker_containers", "concerning", default=[]) or []),
        "high_restart_count": len(_safe_get(health, "docker_containers", "high_restart", default=[]) or []),
    }
    disks = {}
    for key, value in (metrics or {}).items():
        if key.startswith("disk:") and isinstance(value, dict) and "avg" in value:
            disks[key] = value["avg"]
    snap["disks"] = disks
    return snap


def save_snapshot(snapshot: dict) -> str:
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    name = f"{_stamp(_now())}.json"
    path = os.path.join(SNAPSHOTS_DIR, name)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
    return path


def list_snapshot_paths() -> list[str]:
    """Return snapshot file paths sorted oldest-first."""
    if not os.path.isdir(SNAPSHOTS_DIR):
        return []
    return sorted(glob(os.path.join(SNAPSHOTS_DIR, "*.json")))


def _load(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_recent(n: int = KEEP_SNAPSHOTS) -> list[dict]:
    """Return last `n` snapshots, oldest-first."""
    paths = list_snapshot_paths()[-n:]
    return [s for p in paths for s in [_load(p)] if s is not None]


def prune_snapshots(keep: int = KEEP_SNAPSHOTS) -> int:
    """Delete oldest snapshots beyond `keep`. Returns count removed."""
    paths = list_snapshot_paths()
    removed = 0
    for p in paths[:-keep] if len(paths) > keep else []:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


# --- delta computation -------------------------------------------------------

def previous_snapshot(snapshots: list[dict]) -> dict | None:
    """Pick the snapshot to compare 'current' against.

    Prefers the most recent snapshot with a calendar-date earlier than today
    (i.e. literally 'yesterday or earlier'). Falls back to the second-most-
    recent if no older calendar day is available.
    """
    if not snapshots:
        return None
    today = _now().date()
    for s in reversed(snapshots[:-1]):
        try:
            ts = datetime.fromisoformat(s["timestamp"]).date()
        except (ValueError, KeyError, TypeError):
            continue
        if ts < today:
            return s
    return snapshots[-2] if len(snapshots) >= 2 else None


def _format_delta(cur, prev) -> dict | None:
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
        return None
    abs_diff = cur - prev
    pct = (abs_diff / prev * 100) if prev else None
    return {
        "current": round(cur, 2),
        "previous": round(prev, 2),
        "delta_abs": round(abs_diff, 2),
        "delta_pct": round(pct, 1) if pct is not None else None,
    }


SCALAR_KEYS = (
    "cpu_avg", "ram_avg", "network_avg",
    "pending_total", "pending_security",
    "concerning_count", "high_restart_count",
)


def compute_deltas(current: dict, previous: dict | None) -> dict:
    """Return delta annotations vs `previous`. Empty dict if no comparison possible."""
    if not previous:
        return {}
    out = {}
    for key in SCALAR_KEYS:
        d = _format_delta(current.get(key), previous.get(key))
        if d is not None:
            out[key] = d
    disk_deltas = {}
    cur_disks = current.get("disks") or {}
    prev_disks = previous.get("disks") or {}
    for mount, cur_val in cur_disks.items():
        d = _format_delta(cur_val, prev_disks.get(mount))
        if d is not None:
            disk_deltas[mount] = d
    if disk_deltas:
        out["disks"] = disk_deltas
    out["compared_to"] = previous.get("timestamp")
    return out


# --- forecasting -------------------------------------------------------------

def forecast_disk_fill(snapshots: list[dict], mount: str) -> dict | None:
    """Linear regression on disk usage across snapshots → projected fill date.

    Returns None if fewer than 3 data points, slope is non-positive, or the
    projection is implausible (>10 years out). Otherwise returns
    {days_until_full, current_pct, rate_pct_per_day}.
    """
    points = []
    for s in snapshots:
        usage = (s.get("disks") or {}).get(mount)
        ts = s.get("timestamp")
        if not isinstance(usage, (int, float)) or not ts:
            continue
        try:
            t = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            continue
        points.append((t, float(usage)))

    if len(points) < 3:
        return None

    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_xx = sum(p[0] * p[0] for p in points)
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    if slope <= 0:
        return None

    last_t, last_y = points[-1]
    if last_y >= 100:
        return {"days_until_full": 0, "current_pct": round(last_y, 1),
                "rate_pct_per_day": round(slope * 86400, 4)}

    fill_t = (100 - intercept) / slope
    days = (fill_t - last_t) / 86400
    if days <= 0 or days > 3650:
        return None

    return {
        "days_until_full": round(days, 1),
        "current_pct": round(last_y, 1),
        "rate_pct_per_day": round(slope * 86400, 4),
    }


def all_disk_forecasts(snapshots: list[dict]) -> dict:
    """Compute a forecast for every mount we have data for."""
    if not snapshots:
        return {}
    mounts = set()
    for s in snapshots:
        mounts.update((s.get("disks") or {}).keys())
    out = {}
    for mount in mounts:
        f = forecast_disk_fill(snapshots, mount)
        if f is not None:
            out[mount] = f
    return out


# --- sparklines & series ----------------------------------------------------

def render_sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARKLINE_BLOCKS[3] * len(values)
    span = hi - lo
    steps = len(SPARKLINE_BLOCKS) - 1
    return "".join(SPARKLINE_BLOCKS[int((v - lo) / span * steps)] for v in values)


def metric_series(snapshots: list[dict], key: str) -> list[float]:
    """Pull a single numeric metric across snapshots in chronological order."""
    out = []
    for s in snapshots:
        if key.startswith("disk:"):
            v = (s.get("disks") or {}).get(key)
        else:
            v = s.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out
