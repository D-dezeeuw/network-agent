# Network Agent — Implementation Guide
> AI-powered server monitoring agent with Telegram reporting and security news digests.

---

## Overview

A Python-based agent that runs on the Hetzner server, collects metrics from the Netdata API, parses system logs, fetches CVE/security news relevant to the stack, and sends a daily Telegram digest powered by Claude AI.

**Stack:** Python 3.11+, Docker, Netdata REST API, Anthropic API, Telegram Bot API, APScheduler

---

## Project Structure

```
network-agent/
├── Dockerfile
├── docker-compose.yml
├── .env
├── requirements.txt
├── agent/
│   ├── __init__.py
│   ├── main.py              # Entry point, scheduler
│   ├── config.py            # Env vars, constants
│   ├── netdata.py           # Netdata API client
│   ├── logs.py              # Log parser (auth.log, journalctl)
│   ├── security_news.py     # RSS/CVE feed fetcher
│   ├── ai.py                # Claude analysis layer
│   └── telegram.py          # Telegram bot sender
└── tests/
    ├── test_netdata.py
    └── test_logs.py
```

---

## Epic 1 — Project Setup & Configuration

### Story 1.1 — Environment & Dependencies

**As a** developer,  
**I want** a clean Docker-based project setup,  
**So that** the agent runs consistently on the server without polluting the host.

#### Tasks
- [ ] Create `requirements.txt`
- [ ] Create `Dockerfile`
- [ ] Create `docker-compose.yml` with volume mounts for log access
- [ ] Create `.env` with all secrets

#### `requirements.txt`
```txt
anthropic>=0.25.0
python-telegram-bot>=21.0
requests>=2.31.0
feedparser>=6.0.10
apscheduler>=3.10.0
python-dotenv>=1.0.0
```

#### `config.py`
```python
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
NETDATA_BASE_URL = os.getenv("NETDATA_BASE_URL", "http://localhost:19999")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "8"))  # 08:00 daily
LOG_PATH = os.getenv("LOG_PATH", "/host/logs/auth.log")
```

#### `.env`
```env
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
NETDATA_BASE_URL=http://netdata:19999
REPORT_HOUR=8
```

#### ⚠️ Pitfalls
- Never commit `.env` — add it to `.gitignore` immediately
- `TELEGRAM_CHAT_ID` is your personal chat ID, not the bot ID. Get it by messaging `@userinfobot` on Telegram
- Netdata URL inside Docker should use the **container name**, not `localhost`

---

## Epic 2 — Netdata Metrics Collection

### Story 2.1 — Pull Key Metrics from Netdata API

**As a** monitoring agent,  
**I want** to collect CPU, RAM, disk, and network metrics from the last 24 hours,  
**So that** I can detect anomalies and spikes.

#### Netdata API Basics
```
GET /api/v1/charts                          # List all available charts
GET /api/v1/data?chart=system.cpu&after=-86400   # Last 24h of CPU
GET /api/v1/data?chart=system.ram&after=-86400
GET /api/v1/data?chart=system.net&after=-86400
GET /api/v1/data?chart=disk_space._&after=-86400
GET /api/v1/alarms?active=true              # Currently firing alarms
```

#### `netdata.py`
```python
import requests
from config import NETDATA_BASE_URL

CHARTS = {
    "cpu": "system.cpu",
    "ram": "system.ram",
    "network": "system.net",
    "disk": "disk_space._",
}

def fetch_chart(chart: str, after: int = -86400) -> dict:
    """Fetch chart data for the last `after` seconds."""
    url = f"{NETDATA_BASE_URL}/api/v1/data"
    params = {"chart": chart, "after": after, "format": "json"}
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
    return {
        name: fetch_chart(chart)
        for name, chart in CHARTS.items()
    }
```

#### ⚠️ Pitfalls
- Netdata returns **a lot** of data points. Summarize before sending to Claude — don't dump raw JSON into the prompt
- Use `points=24` query param to limit to 24 data points (1 per hour) instead of full resolution
- Always handle `requests.RequestException` — if Netdata is down the agent should still run

#### Focus: Summarizing Metrics
```python
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
```

---

## Epic 3 — Log Parsing

### Story 3.1 — Parse Auth Logs for Suspicious Activity

**As a** security monitor,  
**I want** to scan `auth.log` for failed logins and new SSH sessions,  
**So that** I can flag brute force attempts or unauthorized access.

#### `logs.py`
```python
import subprocess
from datetime import datetime, timedelta

def get_auth_log_summary(hours: int = 24) -> dict:
    """Read last N hours of auth.log entries."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", "ssh", "--since", f"{hours} hours ago", "--no-pager"],
            capture_output=True, text=True, timeout=15
        )
        lines = result.stdout.splitlines()
    except Exception as e:
        print(f"[logs] journalctl failed: {e}")
        lines = []

    failed = [l for l in lines if "Failed password" in l or "Invalid user" in l]
    accepted = [l for l in lines if "Accepted" in l]

    # Extract IPs from failed attempts
    import re
    ips = re.findall(r'from (\d+\.\d+\.\d+\.\d+)', "\n".join(failed))
    from collections import Counter
    top_ips = Counter(ips).most_common(5)

    return {
        "failed_attempts": len(failed),
        "successful_logins": len(accepted),
        "top_attacker_ips": top_ips,
        "raw_sample": failed[:5],  # First 5 examples for context
    }
```

#### Docker: Mounting Host Logs
```yaml
# docker-compose.yml
volumes:
  - /var/log:/host/logs:ro
```

#### ⚠️ Pitfalls
- Mount logs as **read-only** (`:ro`) — agent should never write to host logs
- `journalctl` is cleaner than reading raw files but requires the container to have access to the journal socket
- If journalctl doesn't work inside Docker, fall back to reading `/host/logs/auth.log` directly with Python's file IO

---

## Epic 4 — Security News

### Story 4.1 — Fetch CVE and Security News Relevant to the Stack

**As a** security-aware operator,  
**I want** to receive daily CVE news about Debian, Docker, and nginx,  
**So that** I can act on critical vulnerabilities before they're exploited.

#### RSS Feeds to Monitor
```python
SECURITY_FEEDS = [
    "https://www.debian.org/security/dsa",          # Debian Security Advisories
    "https://feeds.feedburner.com/TheHackersNews",  # General security news
    "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss-analyzed.xml",  # NVD CVEs
]

STACK_KEYWORDS = ["debian", "docker", "nginx", "linux", "kernel", "bookworm", "python"]
```

#### `security_news.py`
```python
import feedparser
from config import SECURITY_FEEDS, STACK_KEYWORDS

def fetch_security_news(max_items: int = 20) -> list[dict]:
    relevant = []
    for feed_url in SECURITY_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_items]:
                title = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()
                if any(kw in title or kw in summary for kw in STACK_KEYWORDS):
                    relevant.append({
                        "title": entry.get("title"),
                        "link": entry.get("link"),
                        "published": entry.get("published", "unknown"),
                        "summary": entry.get("summary", "")[:300],
                    })
        except Exception as e:
            print(f"[news] Failed to fetch {feed_url}: {e}")
    return relevant
```

#### ⚠️ Pitfalls
- NVD RSS feed can be slow — set a generous timeout or run it async
- Truncate summaries before passing to Claude (300 chars is enough for context)
- RSS feeds sometimes go down — always wrap in try/except and continue gracefully

---

## Epic 5 — AI Analysis Layer

### Story 5.1 — Claude Analyzes All Data and Generates a Report

**As a** server operator,  
**I want** Claude to interpret the raw metrics and logs,  
**So that** I get a human-readable, actionable daily digest instead of raw numbers.

#### `ai.py`
```python
import anthropic
from config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def generate_report(metrics: dict, logs: dict, news: list) -> str:
    prompt = f"""
You are an ops monitoring agent for a Linux server (Debian Bookworm).
Analyze the following data and produce a concise daily digest.

## Server Metrics (last 24h)
{metrics}

## Auth Log Summary (last 24h)
{logs}

## Relevant Security News
{news}

Your report should:
- Start with an overall health status: ✅ Healthy / ⚠️ Warning / 🚨 Critical
- Summarize CPU, RAM, disk usage in plain language
- Flag any anomalies or spikes
- Highlight suspicious login activity (brute force, unknown IPs)
- List any CVEs or news relevant to this stack with a severity indication
- End with 1-3 recommended actions if any

Keep it concise. Use emoji for scannability. This will be sent via Telegram.
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text
```

#### ⚠️ Pitfalls
- **Never dump raw Netdata JSON into the prompt** — summarize first or you'll hit token limits and waste money
- Keep `max_tokens=1024` — Telegram messages have a 4096 char limit
- If the prompt + data exceeds context, prioritize: alarms > logs > metrics > news
- Add a fallback if Claude API is unavailable — send a plain "Agent check failed" Telegram message

---

## Epic 6 — Telegram Integration

### Story 6.1 — Send Daily Digest via Telegram

**As a** server operator,  
**I want** to receive the daily report in Telegram,  
**So that** I get notified without checking a dashboard.

#### Setup
1. Message `@BotFather` on Telegram → `/newbot` → copy the token
2. Message `@userinfobot` → copy your personal chat ID
3. Add both to `.env`

#### `telegram.py`
```python
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram max message length is 4096 chars
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[telegram] Failed to send: {e}")
            return False
    return True
```

#### ⚠️ Pitfalls
- Claude may return markdown that Telegram doesn't support — test formatting or switch to `parse_mode: HTML`
- If report exceeds 4096 chars, split into chunks (handled above)
- Don't hardcode chat ID — different people may want reports on different chats in the future

---

## Epic 7 — Scheduler & Main Loop

### Story 7.1 — Run the Agent Daily at a Configured Time

#### `main.py`
```python
from apscheduler.schedulers.blocking import BlockingScheduler
from config import REPORT_HOUR
from netdata import collect_all_metrics, fetch_active_alarms, summarize_chart
from logs import get_auth_log_summary
from security_news import fetch_security_news
from ai import generate_report
from telegram import send_message

def run_agent():
    print("[agent] Starting daily report collection...")

    # Collect
    raw_metrics = collect_all_metrics()
    metrics_summary = {name: summarize_chart(data) for name, data in raw_metrics.items()}
    metrics_summary["active_alarms"] = fetch_active_alarms()

    logs = get_auth_log_summary(hours=24)
    news = fetch_security_news()

    # Analyze
    report = generate_report(metrics_summary, logs, news)

    # Send
    success = send_message(report)
    print(f"[agent] Report sent: {success}")

if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(run_agent, "cron", hour=REPORT_HOUR, minute=0)
    print(f"[agent] Scheduled daily at {REPORT_HOUR}:00")

    # Run immediately on startup for testing
    run_agent()

    scheduler.start()
```

---

## Epic 8 — Dockerization

### `Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
CMD ["python", "agent/main.py"]
```

### `docker-compose.yml`
```yaml
version: "3.8"

services:
  network-agent:
    build: .
    container_name: network-agent
    restart: unless-stopped
    env_file: .env
    volumes:
      - /var/log:/host/logs:ro
    networks:
      - monitoring

networks:
  monitoring:
    external: true  # Same network as Netdata container
```

#### ⚠️ Pitfalls
- The agent needs to be on the **same Docker network as Netdata** to reach it via container name
- Use `restart: unless-stopped` so it survives server reboots
- Log output goes to Docker logs: `docker logs network-agent -f`

---

## Testing Checklist

Before deploying:

- [ ] `python agent/netdata.py` — prints metric summaries
- [ ] `python agent/logs.py` — prints auth log summary
- [ ] `python agent/security_news.py` — prints relevant news items
- [ ] `python agent/telegram.py` — sends a test message to your chat
- [ ] `python agent/main.py` — runs full cycle, sends complete report
- [ ] Restart container — verify report still arrives next scheduled time

---

## Future Enhancements (Backlog)

| Story | Description | Priority |
|---|---|---|
| Instant alerts | Telegram alert when alarm fires in Netdata (webhook) | High |
| Docker container health | Include per-container status in report | Medium |
| Weekly trend report | Compare this week vs last week | Medium |
| Multiple chat targets | Send to group chat or different users | Low |
| Web dashboard | Simple Flask UI showing report history | Low |
