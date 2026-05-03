import requests
from config import NETDATA_BASE_URL

CHARTS = {
    "cpu": "system.cpu",
    "ram": "system.ram",
    "network": "system.net",
    "disk": "disk_space._",
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


def collect_all_metrics() -> dict:
    return {name: fetch_chart(chart) for name, chart in CHARTS.items()}


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
