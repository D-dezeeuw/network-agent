import requests
from config import NETDATA_BASE_URL

STATIC_CHARTS = {
    "cpu": "system.cpu",
    "ram": "system.ram",
    "network": "system.net",
}


def fetch_chart(chart: str, after: int = -86400, points: int = 24) -> dict:
    """Fetch chart data for the last `after` seconds, downsampled to `points`."""
    url = f"{NETDATA_BASE_URL}/api/v1/data"
    params = {"chart": chart, "after": after, "points": points, "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"[netdata] Failed to fetch {chart}: {e}")
        return {}


def fetch_active_alarms() -> list:
    url = f"{NETDATA_BASE_URL}/api/v1/alarms"
    try:
        r = requests.get(url, params={"active": "true"}, timeout=10)
        r.raise_for_status()
        return list(r.json().get("alarms", {}).values())
    except requests.RequestException as e:
        print(f"[netdata] Failed to fetch alarms: {e}")
        return []


def discover_disk_charts() -> list[str]:
    """Returns all disk_space.* chart names available on this Netdata."""
    try:
        r = requests.get(f"{NETDATA_BASE_URL}/api/v1/charts", timeout=10)
        r.raise_for_status()
        return sorted(c for c in r.json().get("charts", {}) if c.startswith("disk_space."))
    except requests.RequestException as e:
        print(f"[netdata] Failed to discover charts: {e}")
        return []


def _decode_mount(suffix: str) -> str:
    """Netdata encodes mount points by replacing '/' with '_'. Reverse that."""
    if suffix == "_":
        return "/"
    return "/" + suffix.lstrip("_").replace("_", "/")


def collect_all_metrics() -> dict:
    out = {name: fetch_chart(chart) for name, chart in STATIC_CHARTS.items()}
    for chart_name in discover_disk_charts():
        suffix = chart_name[len("disk_space."):]
        key = f"disk:{_decode_mount(suffix)}"
        out[key] = fetch_chart(chart_name)
    return out


def summarize_chart(data: dict) -> dict:
    """Extract min, max, avg from chart data."""
    if not data or "data" not in data:
        return {}
    values = [row[1] for row in data["data"] if row[1] is not None]
    if not values:
        return {}
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
    }


if __name__ == "__main__":
    raw = collect_all_metrics()
    for name, data in raw.items():
        print(f"{name}: {summarize_chart(data)}")
    print(f"active_alarms: {fetch_active_alarms()}")
