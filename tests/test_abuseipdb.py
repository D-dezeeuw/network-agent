import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

import abuseipdb


def _setup_cache_path(monkeypatch, tmp_path):
    p = tmp_path / "abuseipdb_cache.json"
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_CACHE_PATH", str(p))
    return p


def _api_response(score=100, reports=8000, country="RU"):
    """Stand-in for `requests.get(...).json()`'s shape."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {
        "data": {
            "abuseConfidenceScore": score,
            "countryCode": country,
            "isp": "Evil ISP",
            "domain": "evil.example",
            "usageType": "Data Center/Web Hosting/Transit",
            "totalReports": reports,
            "numDistinctUsers": 200,
            "lastReportedAt": "2026-05-01T12:00:00+00:00",
        }
    }
    return r


# --- _is_fresh ---------------------------------------------------------------

def test_is_fresh_returns_true_for_recent_entry():
    entry = {"fetched_at": datetime.now(timezone.utc).isoformat()}
    assert abuseipdb._is_fresh(entry, ttl_hours=24) is True


def test_is_fresh_returns_false_for_expired_entry():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    assert abuseipdb._is_fresh({"fetched_at": old}, ttl_hours=24) is False


def test_is_fresh_handles_missing_or_malformed_timestamp():
    assert abuseipdb._is_fresh({}, ttl_hours=24) is False
    assert abuseipdb._is_fresh({"fetched_at": "garbage"}, ttl_hours=24) is False


# --- lookup happy path -------------------------------------------------------

def test_lookup_calls_api_and_caches(monkeypatch, tmp_path):
    cache_path = _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")

    with patch("abuseipdb.requests.get", return_value=_api_response()) as get:
        rec = abuseipdb.lookup("1.2.3.4")

    assert rec is not None
    assert rec["abuse_score"] == 100
    assert rec["country"] == "RU"
    assert rec["total_reports"] == 8000
    assert "fetched_at" in rec

    # API hit
    args = get.call_args
    assert args.kwargs["headers"]["Key"] == "test-key"
    assert args.kwargs["params"]["ipAddress"] == "1.2.3.4"

    # Cache persisted
    cached = json.loads(cache_path.read_text())
    assert cached["1.2.3.4"]["abuse_score"] == 100


def test_lookup_uses_cache_when_fresh(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")

    # First call → API
    with patch("abuseipdb.requests.get", return_value=_api_response()) as get:
        abuseipdb.lookup("1.2.3.4")
        first_call_count = get.call_count

    # Second call → cache (no API)
    with patch("abuseipdb.requests.get", return_value=_api_response()) as get:
        rec = abuseipdb.lookup("1.2.3.4")
        assert get.call_count == 0  # didn't hit the network

    assert first_call_count == 1
    assert rec is not None
    assert rec["abuse_score"] == 100


def test_lookup_re_queries_after_ttl_expires(monkeypatch, tmp_path):
    cache_path = _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")

    # Seed cache with stale entry
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    cache_path.write_text(json.dumps({
        "1.2.3.4": {"abuse_score": 50, "fetched_at": stale},
    }))

    with patch("abuseipdb.requests.get",
               return_value=_api_response(score=99)) as get:
        rec = abuseipdb.lookup("1.2.3.4")

    assert get.call_count == 1
    assert rec["abuse_score"] == 99  # fresh value, not the stale 50


# --- lookup degraded paths ---------------------------------------------------

def test_lookup_returns_none_without_api_key(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", None)
    assert abuseipdb.lookup("1.2.3.4") is None


def test_lookup_returns_none_on_request_failure(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")
    with patch("abuseipdb.requests.get",
               side_effect=requests.RequestException("network down")):
        assert abuseipdb.lookup("1.2.3.4") is None


def test_lookup_returns_none_for_non_string_ip():
    assert abuseipdb.lookup(None) is None
    assert abuseipdb.lookup("") is None
    assert abuseipdb.lookup("   ") is None


def test_lookup_returns_none_on_malformed_json(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")
    bad = MagicMock()
    bad.raise_for_status.return_value = None
    bad.json.side_effect = ValueError("not json")
    with patch("abuseipdb.requests.get", return_value=bad):
        assert abuseipdb.lookup("1.2.3.4") is None


# --- _collect_ips ------------------------------------------------------------

def test_collect_ips_dedupes_across_sources():
    auth = {
        "top_attacker_ips": [["1.2.3.4", 30], ["5.6.7.8", 10]],
        "top_probe_ips": [["1.2.3.4", 5]],
    }
    fail2ban = {"top_banned_ips_24h": [["5.6.7.8", 2]],
                "recent_sample": [{"ip": "9.9.9.9"}]}
    ips = abuseipdb._collect_ips(auth, fail2ban)
    # 1.2.3.4 is the heaviest hitter (35 total) — should come first
    assert ips[0] == "1.2.3.4"
    assert set(ips) == {"1.2.3.4", "5.6.7.8", "9.9.9.9"}


def test_collect_ips_handles_none_inputs():
    assert abuseipdb._collect_ips(None, None) == []


def test_collect_ips_skips_malformed_pairs():
    auth = {"top_attacker_ips": [["only_one_field"], None, ["good", 5]]}
    assert abuseipdb._collect_ips(auth, {}) == ["good"]


# --- lookup_many cap ---------------------------------------------------------

def test_lookup_many_respects_limit(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")
    with patch("abuseipdb.requests.get", return_value=_api_response()) as get:
        out = abuseipdb.lookup_many(
            ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"],
            limit=2,
        )
    assert get.call_count == 2
    assert len(out) == 2


def test_lookup_many_returns_empty_without_key(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", None)
    assert abuseipdb.lookup_many(["1.1.1.1"]) == {}


# --- enrich ------------------------------------------------------------------

def test_enrich_returns_empty_without_key(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", None)
    assert abuseipdb.enrich(
        {"top_attacker_ips": [["1.2.3.4", 99]]}, {}
    ) == {}


def test_enrich_returns_dict_keyed_by_ip(monkeypatch, tmp_path):
    _setup_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(abuseipdb, "ABUSEIPDB_API_KEY", "test-key")
    with patch("abuseipdb.requests.get", return_value=_api_response()):
        out = abuseipdb.enrich(
            {"top_attacker_ips": [["1.2.3.4", 30]]},
            {"top_banned_ips_24h": [["5.6.7.8", 5]]},
        )
    assert "1.2.3.4" in out
    assert "5.6.7.8" in out
    assert out["1.2.3.4"]["abuse_score"] == 100
