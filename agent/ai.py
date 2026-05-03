import json

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from tools import TOOLS_SCHEMA, execute_tool

client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

MAX_TOOL_ITERATIONS = 6


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
    * system health: docker containers in `concerning` (unhealthy health check, dead, restart-looping, or crashed with non-zero exit). DO NOT flag containers in `all_containers` that are merely "exited" with exit code 0 — those are clean one-shot tasks. Also flag `high_restart` and `stale_images_90d` lists.
    * system health: kernel_messages notable count > 0 with HARD failure signals (drive offline, "I/O error", FS read-only remount, OOM kill). Treat "degraded" or warning-level disk states as informational only — do NOT escalate them.

- DISK POLICY: Disk usage/capacity numbers go in the metrics summary as plain info. Only escalate disks to Warning/Critical on evidence of an actual failure (drive offline, FS error in kernel log, read-only remount). High-usage-but-still-OK or "degraded" SMART status are NOT failures — mention briefly but do not raise the overall status level.
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


_QA_SYSTEM_PROMPT = """\
You are an ops assistant for a Debian Bookworm Linux server. The user is the
server's admin asking about its current state.

You have tools that read live data: server metrics, security scan diffs,
system health, Docker containers, auth logs, kernel messages, and CVE news.
Call the tools needed to answer the question accurately. Don't guess —
if you don't have a tool for what was asked, say so.

Reply in plain text suitable for Telegram (Markdown is fine but no HTML).
Keep replies tight. If a tool returns an error, surface it briefly.
"""


def answer_question(user_message: str) -> str:
    """Run a tool-call loop to answer a user question with live server data."""
    messages = [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                max_tokens=1024,
                messages=messages,
                tools=TOOLS_SCHEMA,
            )
        except Exception as e:
            print(f"[ai] OpenRouter API error in answer_question: {e}")
            return f"🚨 Couldn't reach OpenRouter: {e}"

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []

        if not tool_calls:
            return msg.content or "(no answer)"

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    return "🤔 I gathered a lot of data but couldn't converge on an answer. Try a more specific question."
