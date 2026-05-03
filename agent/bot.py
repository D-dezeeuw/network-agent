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
from config import TELEGRAM_AUTHORIZED_USERS, TELEGRAM_BOT_TOKEN

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
    "<b>/acks</b> — list active snoozes\n"
    "<b>/unsnooze &lt;id&gt;</b> — remove a snooze by fingerprint\n"
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
    BotCommand("acks", "List active snoozes"),
    BotCommand("unsnooze", "Remove a snooze (takes id arg)"),
    BotCommand("runnow", "Trigger full digest now"),
    BotCommand("help", "Show this command list"),
]


def _is_authorized(user_id: int | None) -> bool:
    if not TELEGRAM_AUTHORIZED_USERS:
        return False
    return user_id in TELEGRAM_AUTHORIZED_USERS


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
    if not _is_authorized(update.effective_user.id if update.effective_user else None):
        await _refuse(update)
        return
    await update.effective_chat.send_message(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_runnow(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id if update.effective_user else None):
        await _refuse(update)
        return
    await update.effective_chat.send_message("Running a full digest now…")
    from main import run_agent  # avoid circular import at module load
    await asyncio.to_thread(run_agent)
    await update.effective_chat.send_message("Digest sent.")


async def cmd_acks(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id if update.effective_user else None):
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
    if not _is_authorized(update.effective_user.id if update.effective_user else None):
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
        if not _is_authorized(update.effective_user.id if update.effective_user else None):
            await _refuse(update)
            return
        log.info("Slash query for user_id=%s: %r", update.effective_user.id, query)
        try:
            answer = await asyncio.to_thread(answer_question, query)
        except Exception as e:
            log.exception("answer_question raised")
            answer = f"🚨 Internal error: {e}"
        await _send_chunked(update, answer)
    return handler


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_authorized(user.id if user else None):
        await _refuse(update)
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    log.info("Q&A from user_id=%s: %r", user.id, text[:200])
    try:
        answer = await asyncio.to_thread(answer_question, text)
    except Exception as e:
        log.exception("answer_question raised")
        answer = f"🚨 Internal error: {e}"

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
    for cmd, query in COMMAND_QUERIES.items():
        app.add_handler(CommandHandler(cmd, _make_query_handler(query)))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^ack:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
