from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def generate_report(metrics: dict, logs: dict, news: list, security: dict, health: dict) -> str:
    prompt = f"""
You are an ops monitoring agent for a Linux server (Debian Bookworm).
Analyze the following data and produce a concise daily digest.

## Host Security Scan (delta vs baseline)
{security}

## System Health
{health}

## Server Metrics (last 24h)
{metrics}

## Auth Log Summary (last 24h)
{logs}

## Relevant Security News
{news}

Your report should:
- Start with an overall health status: ✅ Healthy / ⚠️ Warning / 🚨 Critical
- HIGHEST PRIORITY (treat as Critical, lead with these):
    * security scan: any authorized_keys / cron / systemd delta
    * security scan: ld_so_preload populated or changed
    * security scan: suspicious processes (running from /tmp, /var/tmp, /dev/shm, or with deleted exe)
    * security scan: new listening ports
- HIGH PRIORITY (treat as Warning):
    * system health: reboot_required true (kernel update pending)
    * system health: pending_updates.security > 0 (list a few package names)
    * system health: docker containers in unhealthy / high_restart / stale_images_90d lists
    * system health: kernel_messages notable count > 0 (OOM, drive errors, etc.)
- If `baseline_established: true`, this is the first scan — confirm baseline is set, do not raise alerts on the security section
- Summarize CPU, RAM, disk usage in plain language
- Flag any anomalies or spikes in metrics
- Highlight suspicious login activity (brute force, unknown IPs)
- Cross-reference: if pending security updates exist AND a CVE in the news matches the package name, call this out explicitly
- End with 1-3 recommended actions if any

Keep it concise. Use emoji for scannability. This will be sent via Telegram.
"""

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[ai] OpenRouter API error: {e}")
        return f"🚨 Agent check failed: OpenRouter API unavailable ({e})"
