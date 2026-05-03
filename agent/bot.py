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
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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


async def _post_init(app: Application) -> None:
    """Register the slash-command menu so Telegram clients show '/' suggestions."""
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
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("runnow", cmd_runnow))
    for cmd, query in COMMAND_QUERIES.items():
        app.add_handler(CommandHandler(cmd, _make_query_handler(query)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
