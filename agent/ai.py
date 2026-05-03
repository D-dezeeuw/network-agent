import json

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from tools import TOOLS_SCHEMA, execute_tool

client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

MAX_TOOL_ITERATIONS = 6

SECTION_MARKERS = ("##STATUS##", "##SECURITY##", "##HEALTH##", "##METRICS##")


def split_report(report: str) -> list[str]:
    """Split a marked-up digest into per-section messages.

    Looks for SECTION_MARKERS at the start of lines, strips them, and
    returns the bodies in document order. If no markers are present
    (Claude didn't follow the format), returns the whole thing as one
    message so we never lose output.
    """
    if not report:
        return ["(empty report)"]

    positions = []
    for marker in SECTION_MARKERS:
        idx = report.find(marker)
        if idx >= 0:
            positions.append((idx, marker))
    if not positions:
        return [report.strip()]

    positions.sort()
    parts: list[str] = []
    for i, (idx, marker) in enumerate(positions):
        start = idx + len(marker)
        end = positions[i + 1][0] if i + 1 < len(positions) else len(report)
        body = report[start:end].strip()
        if body:
            parts.append(body)
    return parts or [report.strip()]


def generate_report(metrics: dict, logs: dict, news: list, security: dict,
                    health: dict, trends: dict | None = None) -> list[str]:
    """Generate the daily digest as an ordered list of section messages."""
    trends_block = trends or {"deltas": {}, "disk_forecasts": {}}
    prompt = f"""\
You are an ops monitoring agent for a Linux server (Debian Bookworm).
Analyze the data below and produce a digest in EXACTLY four sections,
each starting on its own line with the literal marker shown. Output
nothing outside these sections.

Output format:

##STATUS##
A short overall verdict using one of: <b>✅ Healthy</b>, <b>⚠️ Warning</b>,
<b>🚨 Critical</b>. Then 1–2 sentences describing the headline state.

##SECURITY##
Security scan findings — authorized_keys / cron / systemd / ld_so_preload
deltas, suspicious processes, new listening ports. If
<code>baseline_established=true</code> or there are no deltas, give a single
short reassurance line.

##HEALTH##
System health: reboot_required, pending updates (highlight security
count), Docker concerning containers, notable kernel messages.

##METRICS##
CPU/RAM/disk usage in plain language, then any relevant CVE news entries.
Cross-reference: if a pending security update package matches a CVE in
the news, call that out explicitly.

Trend annotations:
- If `trends.deltas` is non-empty, weave at least 3 of the deltas into
  the relevant section (e.g. "CPU avg 23.5% (+12% vs prev)"). Treat
  positive deltas in `pending_security` or `concerning_count` as alerts.
- If `trends.disk_forecasts` has entries with low `days_until_full`
  (under ~30), call them out under METRICS as a capacity warning.

Formatting rules:
- Use Telegram HTML only: <b>, <i>, <code>, <pre>. NO Markdown asterisks.
- Escape literal &lt;, &gt;, &amp; in any non-tag output.
- Each section concise — total under 3500 chars.

Escalation rules:
- HIGHEST PRIORITY (Critical): security scan deltas, ld_so_preload
  populated/changed, suspicious processes, new listening ports.
- HIGH PRIORITY (Warning): reboot_required true, security updates &gt; 0,
  Docker `concerning` (unhealthy/dead/restarting/exit&gt;0), `high_restart`,
  `stale_images_90d`, kernel messages with hard-failure signals.
- DO NOT flag: clean-exited containers (in `all_containers` only),
  warning-level disk states. Disk capacity goes in METRICS as info only —
  escalate disks only on actual failure (drive offline, FS error in kmsg,
  read-only remount).
- If <code>baseline_established=true</code> the security section is informational only.

## Host Security Scan (delta vs baseline)
{security}

## System Health
{health}

## Server Metrics (last 24h)
{metrics}

## Trends (vs prior snapshot)
{trends_block}

## Auth Log Summary (last 24h)
{logs}

## Relevant Security News
{news}
"""

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=1536,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
    except Exception as e:
        print(f"[ai] OpenRouter API error: {e}")
        return [f"🚨 <b>Agent check failed</b>\nOpenRouter API unavailable: {e}"]

    return split_report(content)


_QA_SYSTEM_PROMPT = """\
You are an ops assistant for a Debian Bookworm Linux server. The user is
the server's admin asking about its current state.

You have tools that read live data: server metrics, security scan diffs,
system health, Docker containers, auth logs, kernel messages, and CVE
news. Call the tools needed to answer the question accurately. Don't
guess — if you don't have a tool for what was asked, say so.

Reply in Telegram-supported HTML only: <b>, <i>, <code>, <pre>. Escape
literal &lt;, &gt;, &amp; in any non-tag output. Keep replies tight. If
a tool returns an error, surface it briefly.
"""


def _format_tool_footer(tools_used: list[str]) -> str:
    if not tools_used:
        return ""
    unique = sorted(set(tools_used))
    return f"\n\n<i>used: {', '.join(unique)}</i>"


def answer_question(user_message: str) -> str:
    """Run a tool-call loop to answer a user question with live server data."""
    messages = [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    tools_used: list[str] = []

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
            return (msg.content or "(no answer)") + _format_tool_footer(tools_used)

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
            tools_used.append(tc.function.name)
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

    return ("🤔 I gathered a lot of data but couldn't converge on an answer. "
            "Try a more specific question.") + _format_tool_footer(tools_used)
