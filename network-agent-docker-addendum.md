# Network Agent — Docker Addendum
> Addendum to the main implementation guide. Covers everything needed to run the agent as a Docker container on the Hetzner server alongside Netdata and Nginx Proxy Manager.

---

## Prerequisites

- Docker 20.10+ (you're on 29.4.2 ✅)
- Netdata container already running
- Agent code complete and tested locally

---

## 1. Identify the Netdata Network

The agent needs to reach Netdata by container name, so they must share a Docker network. Find which network Netdata is on:

```bash
docker inspect netdata --format '{{json .NetworkSettings.Networks}}' | python3 -m json.tool
```

Note the network name — you'll use it in `docker-compose.yml`. Common names are `bridge`, `monitoring`, or something custom.

If Netdata is on the default `bridge` network, create a dedicated one instead:

```bash
docker network create monitoring
docker network connect monitoring netdata
```

> ⚠️ The default `bridge` network does **not** support container name DNS resolution. A named network does. This is a common gotcha.

---

## 2. Final `docker-compose.yml`

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
      - /run/log/journal:/run/log/journal:ro   # journalctl access
      - /etc/machine-id:/etc/machine-id:ro     # required for journalctl
    networks:
      - monitoring

networks:
  monitoring:
    external: true
```

> The three volume mounts give the container read-only access to host logs **and** `journalctl`. Without `machine-id` and the journal socket, `journalctl` inside the container won't work.

---

## 3. Final `Dockerfile`

```dockerfile
FROM python:3.11-slim

# Install systemd journal client for journalctl access
RUN apt-get update && apt-get install -y \
    systemd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

CMD ["python", "agent/main.py"]
```

> ⚠️ The `systemd` package is needed inside the container to read the host journal. Without it `journalctl` won't be available. If you hit issues, fall back to reading `/host/logs/auth.log` directly in `logs.py`.

---

## 4. Environment Variables

Your `.env` file on the **host** (next to `docker-compose.yml`):

```env
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
NETDATA_BASE_URL=http://netdata:19999
REPORT_HOUR=8
LOG_PATH=/host/logs/auth.log
```

Key point: `NETDATA_BASE_URL` uses the **container name** `netdata`, not `localhost`. This only works because both containers are on the same named network.

---

## 5. Build & Run

```bash
# Build the image
docker build -t network-agent .

# Start via compose
docker compose up -d

# Verify it's running
docker ps | grep network-agent

# Watch live logs
docker logs network-agent -f
```

On first start, the agent runs immediately (by design in `main.py`) so you'll see a full cycle in the logs right away.

---

## 6. Verify Network Connectivity

From inside the running container, confirm it can reach Netdata:

```bash
docker exec -it network-agent curl http://netdata:19999/api/v1/info
```

Should return a JSON response with Netdata version info. If it times out, the network connection is wrong — recheck step 1.

---

## 7. Updating the Agent

When you make code changes:

```bash
# Rebuild and recreate
docker compose up -d --build

# Or force recreate without cache
docker compose build --no-cache && docker compose up -d
```

> ⚠️ `docker compose restart` does **not** rebuild the image — always use `up -d --build` after code changes.

---

## 8. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Connection refused` to Netdata | Wrong network or URL | Check `NETDATA_BASE_URL` and shared network |
| `journalctl` returns nothing | Missing volume mounts | Add journal + machine-id mounts |
| Telegram message not arriving | Wrong token or chat ID | Test with `curl` directly against Telegram API |
| Container exits immediately | Python error on startup | Check `docker logs network-agent` |
| Report arrives but no metrics | Netdata unreachable, agent continues anyway | Check `netdata.py` error logs |
| `claude-sonnet` API error | Invalid or missing API key | Verify `ANTHROPIC_API_KEY` in `.env` |

---

## 9. Portainer Integration

Since you're running Portainer, you can manage the agent from the UI:

1. Go to **Stacks** → **Add stack**
2. Paste the `docker-compose.yml` contents
3. Add env vars under **Environment variables**
4. Deploy

This avoids SSH for day-to-day management. Logs are also viewable directly in Portainer under the container.

---

## 10. Testing Without Waiting for the Schedule

To trigger a report manually without waiting for the cron:

```bash
docker exec -it network-agent python agent/main.py
```

Or add a one-shot run mode to `main.py` via an env var:

```python
import os
if os.getenv("RUN_NOW", "false").lower() == "true":
    run_agent()
else:
    scheduler.start()
```

Then trigger it with:
```bash
docker run --rm --env-file .env -e RUN_NOW=true network-agent
```
