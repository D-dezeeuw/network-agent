"""AbuseIPDB lookup integration.

Enriches IPs that show up in our auth log and fail2ban data with their
public abuse confidence score so the digest can say "this IP is on 8000
blocklists" rather than just "this IP scanned us 30 times".

Read-only against AbuseIPDB — reporting is a separate concern.

Cache: per-IP entries persist to /state/abuseipdb_cache.json with a
configurable TTL (default 24h). The free-tier 1000 lookups/day budget
is generous, but caching keeps us from re-paying for the same scanner
across consecutive cycles.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from config import (
    ABUSEIPDB_API_KEY,
    ABUSEIPDB_CACHE_PATH,
    ABUSEIPDB_CACHE_TTL_HOURS,
    ABUSEIPDB_LOOKUP_LIMIT,
)
from overrides import effective_int

API_URL = "https://api.abuseipdb.com/api/v2/check"
REQUEST_TIMEOUT_S = 8

log = logging.getLogger("abuseipdb")


# --- cache --------------------------------------------------------------------

def _load_cache() -> dict:
    if not os.path.exists(ABUSEIPDB_CACHE_PATH):
        return {}
    try:
        with open(ABUSEIPDB_CACHE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("cache read failed (%s); starting fresh", e)
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(ABUSEIPDB_CACHE_PATH), exist_ok=True)
        with open(ABUSEIPDB_CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError as e:
        log.warning("cache write failed: %s", e)


def _is_fresh(entry: dict, ttl_hours: int) -> bool:
    fetched = entry.get("fetched_at")
    if not isinstance(fetched, str):
        return False
    try:
        ts = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    except ValueError:
        return False
    return ts > datetime.now(timezone.utc) - timedelta(hours=ttl_hours)


# --- API ----------------------------------------------------------------------

def _check_api(ip: str) -> dict | None:
    """One AbuseIPDB GET. Returns the normalized record or None on any failure."""
    if not ABUSEIPDB_API_KEY:
        return None
    try:
        r = requests.get(
            API_URL,
            headers={"Key": ABUSEIPDB_API_KEY, "Accept": "application/json"},
            params={"ipAddress": ip, "maxAgeInDays": 90},
            timeout=REQUEST_TIMEOUT_S,
        )
        r.raise_for_status()
        payload = r.json().get("data") or {}
    except (requests.RequestException, ValueError) as e:
        log.warning("AbuseIPDB lookup failed for %s: %s", ip, e)
        return None

    return {
        "abuse_score": payload.get("abuseConfidenceScore", 0),
        "country": payload.get("countryCode"),
        "isp": payload.get("isp"),
        "domain": payload.get("domain"),
        "usage_type": payload.get("usageType"),
        "total_reports": payload.get("totalReports", 0),
        "num_distinct_users": payload.get("numDistinctUsers", 0),
        "last_reported_at": payload.get("lastReportedAt"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --- public API ---------------------------------------------------------------

def lookup(ip: str) -> dict | None:
    """Return reputation record for `ip`, using the cache when fresh.

    Returns None when no API key is configured, when the IP isn't an
    obviously valid string, or when the API call failed (we treat all
    failure modes the same — graceful degradation).
    """
    if not ABUSEIPDB_API_KEY or not isinstance(ip, str) or not ip.strip():
        return None
    ttl = effective_int("ABUSEIPDB_CACHE_TTL_HOURS", ABUSEIPDB_CACHE_TTL_HOURS) or 24

    cache = _load_cache()
    if ip in cache and _is_fresh(cache[ip], ttl):
        return cache[ip]

    record = _check_api(ip)
    if record is None:
        return None

    cache[ip] = record
    _save_cache(cache)
    return record


def lookup_many(ips: list[str], limit: int | None = None) -> dict[str, dict]:
    """Batch-lookup with cap to protect daily-quota."""
    if not ABUSEIPDB_API_KEY:
        return {}
    cap = limit if limit is not None else (
        effective_int("ABUSEIPDB_LOOKUP_LIMIT", ABUSEIPDB_LOOKUP_LIMIT)
        or ABUSEIPDB_LOOKUP_LIMIT
    )
    out: dict[str, dict] = {}
    for ip in ips[:cap]:
        rec = lookup(ip)
        if rec is not None:
            out[ip] = rec
    return out


def _collect_ips(auth_log: dict | None, fail2ban: dict | None) -> list[str]:
    """Pull unique candidate IPs from the auth log and fail2ban summary,
    sorted by aggregate occurrence so we look up the worst offenders first."""
    counts: dict[str, int] = {}

    def _add(pairs):
        for entry in pairs or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                ip, n = entry[0], entry[1]
                if isinstance(ip, str) and isinstance(n, int):
                    counts[ip] = counts.get(ip, 0) + n

    if isinstance(auth_log, dict):
        _add(auth_log.get("top_attacker_ips"))
        _add(auth_log.get("top_probe_ips"))

    if isinstance(fail2ban, dict):
        _add(fail2ban.get("top_banned_ips_24h"))
        for entry in fail2ban.get("recent_sample") or []:
            if isinstance(entry, dict):
                ip = entry.get("ip")
                if isinstance(ip, str):
                    counts[ip] = counts.get(ip, 0) + 1

    # Worst offenders first
    return [ip for ip, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def enrich(auth_log: dict | None, fail2ban: dict | None) -> dict[str, dict]:
    """Look up reputation for the most-active IPs across auth + fail2ban.

    Returns {ip: reputation_record}. Empty dict when no API key, when no
    IPs were found, or when every lookup failed.
    """
    if not ABUSEIPDB_API_KEY:
        return {}
    ips = _collect_ips(auth_log, fail2ban)
    if not ips:
        return {}
    return lookup_many(ips)
