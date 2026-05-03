"""Interactive Telegram listener.

Long-polls Telegram for messages from authorized users and routes them
through the AI tool-call loop. Co-exists with the scheduled digest.
"""

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from acks import active_acks, add_ack, remove_ack
from ai import answer_question
from charts import render_sparkline as render_sparkline_png
from config import TELEGRAM_AUTHORIZED_USERS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from docker_logs import get_container_logs, list_container_names
import memory
from notifications import clear_mute, mute_for, mute_status
from overrides import is_settable, report_config, set_override, unset_override
from tg_publish import html_escape
import tool_mute
from trends import load_recent, metric_series, render_sparkline

log = logging.getLogger("bot")

CHUNK_LIMIT = 4096

# Slash commands that route through the Q&A pipeline. Each maps to a
# canned natural-language query so the AI uses the right tools and
# formats the answer consistently.
COMMAND_QUERIES = {
    "status": "Give me a brief overall status of the server right now.",
    "disks": "How are the disks doing? Capacity per mount and any failure signals.",
    "containers": "What's the state of all Docker containers? Highlight any concerning ones.",
    "security": "Show me the current security scan delta vs baseline.",
    "updates": "Are there pending package updates? Highlight security updates.",
    "news": "Show me relevant security news / CVEs for our stack.",
}

HELP_TEXT = (
    "<b>network-agent commands</b>\n\n"
    "<b>/runnow</b> — full digest now (posts to channel)\n"
    "<b>/status</b> — quick overall status\n"
    "<b>/disks</b> — disk usage and issues\n"
    "<b>/containers</b> — Docker container state\n"
    "<b>/security</b> — security scan delta\n"
    "<b>/updates</b> — pending package updates\n"
    "<b>/news</b> — relevant security news\n"
    "<b>/trend &lt;metric&gt;</b> — sparkline + delta for a metric\n"
    "<b>/chart &lt;metric&gt;</b> — render a chart image for a metric\n"
    "<b>/logs &lt;container&gt; [lines]</b> — recent docker logs (default 100, max 500)\n"
    "<b>/acks</b> — list active snoozes\n"
    "<b>/unsnooze &lt;id&gt;</b> — remove a snooze by fingerprint\n"
    "<b>/mute_all &lt;hours&gt;</b> — silence all output (incl. criticals)\n"
    "<b>/unmute_all</b> — cancel an active mute\n"
    "<b>/mute &lt;source&gt; [N]</b> — silence one source for N digests\n"
    "<b>/unmute &lt;source&gt;</b> — restore a muted source\n"
    "<b>/set &lt;KEY&gt; &lt;VALUE&gt;</b> — runtime config override\n"
    "<b>/unset &lt;KEY&gt;</b> — revert an override\n"
    "<b>/config</b> — show effective settings + sources\n"
    "<b>/preview</b> — dry-run digest sent only to you\n"
    "<b>/clearmemory</b> — forget recent Q&amp;A context\n"
    "<b>/help</b> — this menu\n\n"
    "Or just ask a question in plain English."
)

BOT_COMMAND_MENU = [
    BotCommand("status", "Quick overall status"),
    BotCommand("disks", "Disk usage and issues"),
    BotCommand("containers", "Docker container state"),
    BotCommand("security", "Security scan delta"),
    BotCommand("updates", "Pending package updates"),
    BotCommand("news", "Relevant security news"),
    BotCommand("trend", "Sparkline + delta for a metric"),
    BotCommand("chart", "Render a chart image for a metric"),
    BotCommand("logs", "Recent docker logs for a container"),
    BotCommand("acks", "List active snoozes"),
    BotCommand("unsnooze", "Remove a snooze (takes id arg)"),
    BotCommand("mute_all", "Silence all output for N hours"),
    BotCommand("unmute_all", "Cancel an active mute"),
    BotCommand("mute", "Silence one data source"),
    BotCommand("unmute", "Restore a muted source"),
    BotCommand("set", "Runtime config override"),
    BotCommand("unset", "Revert an override"),
    BotCommand("config", "Show effective settings"),
    BotCommand("preview", "Dry-run digest to caller only"),
    BotCommand("clearmemory", "Forget Q&A context"),
    BotCommand("runnow", "Trigger full digest now"),
    BotCommand("help", "Show this command list"),
]


# /trend metric → snapshot key mapping. Disk metrics pass through as-is
# (e.g. "disk:/var/lib/docker") since they're stored under that exact key.
TREND_METRIC_KEYS = {
    "cpu": "cpu_avg",
    "ram": "ram_avg",
    "network": "network_avg",
    "pending": "pending_total",
    "security_pending": "pending_security",
    "concerning": "concerning_count",
    "high_restart": "high_restart_count",
}


def _resolve_digest_chat_id() -> int | None:
    """TELEGRAM_CHAT_ID arrives as a string from env; coerce once at import."""
    if not TELEGRAM_CHAT_ID:
        return None
    try:
        return int(TELEGRAM_CHAT_ID)
    except ValueError:
        log.warning("TELEGRAM_CHAT_ID is not numeric: %r", TELEGRAM_CHAT_ID)
        return None


_DIGEST_CHAT_ID = _resolve_digest_chat_id()


def _is_authorized(user_id: int | None) -> bool:
    """User-ID-only check. Used by callback queries (button taps always carry a user)."""
    if not TELEGRAM_AUTHORIZED_USERS:
        return False
    return user_id in TELEGRAM_AUTHORIZED_USERS


def _is_authorized_update(update: Update) -> bool:
    """Authorize either by user (private DM) or by chat (digest channel/group).

    Channels don't carry an effective_user on posts, so we additionally trust
    any message arriving in the configured digest chat — admins of that chat
    are already trusted to receive the digest, so they're trusted to issue
    commands there too.
    """
    user = update.effective_user
    if user and _is_authorized(user.id):
        return True
    chat = update.effective_chat
    if chat and _DIGEST_CHAT_ID is not None and chat.id == _DIGEST_CHAT_ID:
        return True
    return False


async def _send_chunked(update: Update, text: str) -> None:
    if not text:
        text = "(empty reply)"
    for i in range(0, len(text), CHUNK_LIMIT):
        await update.effective_chat.send_message(
            text[i:i + CHUNK_LIMIT], parse_mode=ParseMode.HTML,
        )


async def _refuse(update: Update) -> None:
    user = update.effective_user
    log.warning("Unauthorized attempt from user_id=%s username=%s",
                user.id if user else None, user.username if user else None)
    await update.effective_chat.send_message(
        "Sorry — this bot only answers its configured admin."
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    await update.effective_chat.send_message(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_runnow(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    await update.effective_chat.send_message("Running a full digest now…")
    from main import run_agent  # avoid circular import at module load
    await asyncio.to_thread(run_agent)
    await update.effective_chat.send_message("Digest sent.")


async def cmd_acks(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    active = active_acks()
    if not active:
        await update.effective_chat.send_message("No active snoozes.")
        return
    lines = ["<b>Active snoozes:</b>"]
    for fp, info in sorted(active.items(), key=lambda kv: kv[1].get("expires_at", "")):
        label = info.get("label", "(no label)")
        expires = info.get("expires_at", "?")
        lines.append(f"\n<code>{fp}</code> — expires {expires}\n  {label}")
    await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_unsnooze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        await update.effective_chat.send_message(
            "Usage: <code>/unsnooze &lt;fingerprint&gt;</code>\n"
            "(get fingerprints from /acks)",
            parse_mode=ParseMode.HTML,
        )
        return
    fp = args[0].strip()
    if remove_ack(fp):
        await update.effective_chat.send_message(f"Removed snooze for <code>{fp}</code>.",
                                                 parse_mode=ParseMode.HTML)
    else:
        await update.effective_chat.send_message(f"No active snooze for <code>{fp}</code>.",
                                                 parse_mode=ParseMode.HTML)


async def cmd_trend(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        available = ", ".join(sorted(TREND_METRIC_KEYS.keys()))
        await update.effective_chat.send_message(
            "Usage: <code>/trend &lt;metric&gt;</code>\n"
            f"Built-in: <code>{available}</code>\n"
            "Disks: <code>/trend disk:/</code>, <code>/trend disk:/var/lib/docker</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    metric = args[0].strip()
    snap_key = TREND_METRIC_KEYS.get(metric, metric)

    snapshots = load_recent()
    if not snapshots:
        await update.effective_chat.send_message("No snapshots yet — wait for the first digest to land.")
        return

    series = metric_series(snapshots, snap_key)
    if not series:
        await update.effective_chat.send_message(
            f"No data for <code>{metric}</code>. Snapshot key: <code>{snap_key}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    spark = render_sparkline(series)
    latest = series[-1]
    earliest = series[0]
    delta = latest - earliest
    direction = "↑" if delta > 0 else "↓" if delta < 0 else "→"
    pct = (delta / earliest * 100) if earliest else None
    pct_str = f"{pct:+.1f}%" if pct is not None else f"{delta:+.2f}"

    text = (
        f"<b>{metric}</b> over last {len(series)} snapshot(s)\n"
        f"<code>{spark}</code>\n"
        f"Range: {min(series):.2f} – {max(series):.2f}\n"
        f"Latest: {latest:.2f} ({direction} {pct_str} since first)"
    )
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)


async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        available = ", ".join(sorted(TREND_METRIC_KEYS.keys()))
        await update.effective_chat.send_message(
            "Usage: <code>/chart &lt;metric&gt;</code>\n"
            f"Built-in: <code>{available}</code>\n"
            "Disks: <code>/chart disk:/var/lib/docker</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    metric = args[0].strip()
    snap_key = TREND_METRIC_KEYS.get(metric, metric)

    snapshots = load_recent()
    series = metric_series(snapshots, snap_key)
    if len(series) < 2:
        await update.effective_chat.send_message(
            f"Not enough snapshots yet for <code>{metric}</code> "
            f"(have {len(series)}, need ≥2). Wait for more digest cycles.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        png = render_sparkline_png(series, title=f"{metric} ({len(series)} pts)")
    except Exception as e:
        log.exception("chart render failed")
        await update.effective_chat.send_message(f"Chart render failed: {e}")
        return

    # /chart replies live in the chat the command came from, not the digest
    # destination. Use the bot's send_photo path rather than tg_publish.
    try:
        await update.effective_chat.send_photo(
            photo=png,
            caption=f"<b>{metric}</b> latest: {series[-1]:.2f}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("send_photo failed")
        await update.effective_chat.send_message(f"Couldn't send chart: {e}")


LOGS_INLINE_CHAR_CAP = 3500  # Telegram body cap is 4096; leave headroom for HTML wrapper.


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stream the tail of a container's logs into the caller's chat.

    Inline `<pre>` block when it fits; uploads as a `.log` document
    otherwise so longer outputs aren't silently truncated.
    """
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        names = await asyncio.to_thread(list_container_names)
        sample = ", ".join(f"<code>{n}</code>" for n in names[:10])
        more = f" (+{len(names) - 10} more)" if len(names) > 10 else ""
        await update.effective_chat.send_message(
            "Usage: <code>/logs &lt;container&gt; [lines]</code>\n"
            "Default 100 lines, max 500. Substring match works (case-insensitive).\n\n"
            f"<b>Containers:</b> {sample or '(docker unavailable)'}{more}",
            parse_mode=ParseMode.HTML,
        )
        return

    name = args[0].strip()
    tail = 100
    if len(args) >= 2:
        try:
            tail = int(args[1])
        except ValueError:
            await update.effective_chat.send_message(
                f"Bad lines arg: <code>{html_escape(args[1])}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

    try:
        result = await asyncio.to_thread(get_container_logs, name, tail)
    except Exception as e:
        log.exception("get_container_logs raised")
        await update.effective_chat.send_message(f"🚨 {e}")
        return

    if result.get("error"):
        msg = f"❌ {html_escape(result['error'])}"
        if result.get("available"):
            avail = ", ".join(f"<code>{html_escape(n)}</code>" for n in result["available"][:20])
            msg += f"\n\n<b>Available:</b> {avail}"
        await update.effective_chat.send_message(msg, parse_mode=ParseMode.HTML)
        return

    lines = result.get("lines", [])
    body = "\n".join(lines) if lines else "(no log output)"
    header = (
        f"<b>{html_escape(result['name'])}</b> "
        f"({html_escape(result['status'])}) — "
        f"last {result['line_count']} line(s)"
    )

    escaped = html_escape(body)
    if len(escaped) <= LOGS_INLINE_CHAR_CAP:
        await update.effective_chat.send_message(
            f"{header}\n<pre>{escaped}</pre>",
            parse_mode=ParseMode.HTML,
        )
        return

    from io import BytesIO
    buf = BytesIO(body.encode("utf-8"))
    buf.name = f"{result['name']}.log"
    try:
        await update.effective_chat.send_document(
            document=buf,
            caption=f"{header}\n<i>(too long for inline — {len(body)} chars)</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("send_document failed for /logs")
        await update.effective_chat.send_message(
            f"Couldn't upload log file: {html_escape(str(e))}",
            parse_mode=ParseMode.HTML,
        )


async def cmd_mute_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        await update.effective_chat.send_message(
            "Usage: <code>/mute_all &lt;hours&gt;</code>\n"
            "Suppresses ALL agent output (digest + criticals + alarm poller) "
            "until the duration expires.",
            parse_mode=ParseMode.HTML,
        )
        return
    raw = args[0].strip().lower().rstrip("h")
    try:
        hours = float(raw)
    except ValueError:
        await update.effective_chat.send_message(
            f"Couldn't parse <code>{args[0]}</code> as hours.",
            parse_mode=ParseMode.HTML,
        )
        return
    if hours <= 0 or hours > 24 * 30:
        await update.effective_chat.send_message(
            "Pick a duration between 0 and 720 hours (30 days).")
        return
    record = mute_for(hours)
    await update.effective_chat.send_message(
        f"🔕 Muted for {hours}h. Expires at <code>{record['expires_at']}</code>.\n"
        "Use <code>/unmute_all</code> to cancel early.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unmute_all(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    status = mute_status()
    cleared = clear_mute()
    if cleared and status:
        await update.effective_chat.send_message(
            f"🔔 Mute cancelled (was set to expire at <code>{status.get('expires_at')}</code>).",
            parse_mode=ParseMode.HTML,
        )
    elif cleared:
        await update.effective_chat.send_message("🔔 Stale mute file cleared.")
    else:
        await update.effective_chat.send_message("Nothing to unmute — agent isn't muted.")


async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if len(args) < 2:
        await update.effective_chat.send_message(
            "Usage: <code>/set &lt;KEY&gt; &lt;VALUE&gt;</code>\n"
            "Settable: <code>OPENROUTER_MODEL</code>, <code>QUIET_HOURS</code>, "
            "<code>REPORT_HOUR</code>, <code>REPORT_INTERVAL_HOURS</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    key = args[0].strip().upper()
    value = " ".join(args[1:]).strip()
    if not is_settable(key):
        await update.effective_chat.send_message(
            f"<code>{key}</code> isn't settable at runtime.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        stored = set_override(key, value)
    except ValueError as e:
        await update.effective_chat.send_message(f"❌ {e}")
        return
    note = ""
    if key in ("REPORT_HOUR", "REPORT_INTERVAL_HOURS"):
        note = "\n<i>Scheduler trigger is built once at startup — restart the container for cadence changes to take effect.</i>"
    await update.effective_chat.send_message(
        f"✅ <code>{key}</code> = <code>{stored}</code>{note}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        await update.effective_chat.send_message(
            "Usage: <code>/unset &lt;KEY&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    key = args[0].strip().upper()
    if unset_override(key):
        await update.effective_chat.send_message(
            f"✅ Cleared override for <code>{key}</code>.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_chat.send_message(
            f"No override set for <code>{key}</code>.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_config(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    rows = report_config()
    lines = ["<b>Effective config</b>"]
    for r in rows:
        marker = "🔧" if r["source"] == "override" else "📦" if r["source"] == "env" else "⚪"
        lines.append(
            f"{marker} <code>{r['key']}</code> = <code>{r['value']}</code> "
            f"<i>({r['source']})</i>"
        )
    lines.append("\n🔧 override · 📦 env · ⚪ default")
    await update.effective_chat.send_message("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Per-source mute. Distinct from /mute_all (which silences ALL output)."""
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        active = tool_mute.active_mutes()
        avail = ", ".join(tool_mute.known_aliases())
        active_lines = "\n".join(
            f"  <code>{k}</code> — {v if v is not None else '∞'} cycles left"
            for k, v in active.items()
        ) or "  (none)"
        await update.effective_chat.send_message(
            "Usage: <code>/mute &lt;source&gt; [N]</code>\n"
            f"Sources: <code>{avail}</code>\n"
            f"<b>Active mutes:</b>\n{active_lines}",
            parse_mode=ParseMode.HTML,
        )
        return
    alias = args[0].strip().lower()
    source = tool_mute.resolve(alias)
    if source is None:
        await update.effective_chat.send_message(
            f"Unknown source <code>{alias}</code>. "
            f"Known: <code>{', '.join(tool_mute.known_aliases())}</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    cycles: int | None = None
    if len(args) >= 2:
        try:
            cycles = int(args[1])
            if cycles <= 0:
                raise ValueError
        except ValueError:
            await update.effective_chat.send_message(
                f"Couldn't parse <code>{args[1]}</code> as a positive integer.",
                parse_mode=ParseMode.HTML,
            )
            return
    tool_mute.mute(source, cycles)
    horizon = f"{cycles} digest cycle(s)" if cycles else "indefinitely"
    await update.effective_chat.send_message(
        f"🔕 <code>{alias}</code> muted for {horizon}.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    args = ctx.args or []
    if not args:
        await update.effective_chat.send_message(
            "Usage: <code>/unmute &lt;source&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    alias = args[0].strip().lower()
    source = tool_mute.resolve(alias)
    if source is None:
        await update.effective_chat.send_message(
            f"Unknown source <code>{alias}</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    if tool_mute.unmute(source):
        await update.effective_chat.send_message(
            f"🔔 <code>{alias}</code> unmuted.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_chat.send_message(
            f"<code>{alias}</code> wasn't muted.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_preview(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Dry-run a digest, sent only to the caller. Skips snapshot writes
    and tool-mute decrements so it doesn't perturb persistent state."""
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    chat = update.effective_chat
    if chat is None:
        return
    await chat.send_message("Generating preview…")
    from main import run_agent  # avoid circular import
    try:
        await asyncio.to_thread(run_agent, chat.id, True, True)
    except Exception as e:
        log.exception("preview run_agent failed")
        await chat.send_message(f"🚨 Preview failed: {e}")


async def cmd_clearmemory(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return
    user = update.effective_user
    user_id = user.id if user else None
    if user_id is None:
        await update.effective_chat.send_message("No user context to clear.")
        return
    if memory.clear(user_id):
        await update.effective_chat.send_message("🧹 Conversation memory cleared.")
    else:
        await update.effective_chat.send_message("Nothing to clear — no buffer for you yet.")


_SNOOZE_DURATIONS = {"s24": ("24h", 24), "s7d": ("7 days", 24 * 7)}


async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Process inline-button taps on finding messages.

    callback_data format: "ack:<action>:<fingerprint>"
      action ∈ {s24, s7d, inv}
    """
    query = update.callback_query
    if query is None:
        return

    user_id = query.from_user.id if query.from_user else None
    if not _is_authorized(user_id):
        await query.answer("Not authorized.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "ack":
        await query.answer("Bad callback.", show_alert=True)
        return
    _, action, fp = parts

    label = (query.message.text or "")[:200] if query.message else ""

    if action in _SNOOZE_DURATIONS:
        human, hours = _SNOOZE_DURATIONS[action]
        add_ack(fp, label, hours=hours)
        await query.answer(f"Snoozed for {human}.")
        try:
            await query.edit_message_text(
                text=f"{label}\n\n<i>✅ snoozed for {human}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception as e:
            log.warning("edit_message_text failed: %s", e)
    elif action == "inv":
        await query.answer("Marked for investigation.")
        try:
            await query.edit_message_text(
                text=f"{label}\n\n<i>🔍 flagged for investigation</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception as e:
            log.warning("edit_message_text failed: %s", e)
    else:
        await query.answer("Unknown action.", show_alert=True)


def _make_query_handler(query: str):
    """Build a CommandHandler callback that runs `query` through answer_question."""
    async def handler(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized_update(update):
            await _refuse(update)
            return
        user_id = update.effective_user.id if update.effective_user else None
        log.info("Slash query for user_id=%s: %r", user_id, query)
        try:
            answer = await asyncio.to_thread(answer_question, query)
        except Exception as e:
            log.exception("answer_question raised")
            answer = f"🚨 Internal error: {e}"
        await _send_chunked(update, answer)
    return handler


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized_update(update):
        await _refuse(update)
        return

    text = (update.message.text or update.effective_message.text or "").strip() if update.effective_message else ""
    if not text:
        return

    user = update.effective_user
    user_id = user.id if user else None
    history = memory.get_history(user_id) if user_id else []
    log.info("Q&A from user_id=%s: %r (history turns=%d)",
             user_id, text[:200], len(history) // 2)
    try:
        answer = await asyncio.to_thread(answer_question, text, history)
    except Exception as e:
        log.exception("answer_question raised")
        answer = f"🚨 Internal error: {e}"

    if user_id and answer:
        memory.append_turn(user_id, text, answer)
    await _send_chunked(update, answer)


async def register_commands(app: Application) -> None:
    """Register the slash-command menu so Telegram clients show '/' suggestions.

    Called explicitly from main.py after the application initializes. We
    don't use builder.post_init() because PTB v21 only fires that hook
    from Application.run_polling() / run_webhook() — not from the manual
    `async with app: app.start()` flow we use.
    """
    try:
        await app.bot.set_my_commands(BOT_COMMAND_MENU)
        log.info("Registered %d slash commands with Telegram", len(BOT_COMMAND_MENU))
    except Exception as e:
        log.warning("Failed to register slash commands: %s", e)


def build_application() -> Application | None:
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set; bot disabled")
        return None
    if not TELEGRAM_AUTHORIZED_USERS:
        log.info("TELEGRAM_AUTHORIZED_USERS empty; Q&A bot disabled (digest still active)")
        return None

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("runnow", cmd_runnow))
    app.add_handler(CommandHandler("acks", cmd_acks))
    app.add_handler(CommandHandler("unsnooze", cmd_unsnooze))
    app.add_handler(CommandHandler("trend", cmd_trend))
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("mute_all", cmd_mute_all))
    app.add_handler(CommandHandler("unmute_all", cmd_unmute_all))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("unset", cmd_unset))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("clearmemory", cmd_clearmemory))
    for cmd, query in COMMAND_QUERIES.items():
        app.add_handler(CommandHandler(cmd, _make_query_handler(query)))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^ack:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
