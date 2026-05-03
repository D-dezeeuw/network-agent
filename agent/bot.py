"""Interactive Telegram listener.

Long-polls Telegram for messages from authorized users and routes them
through the AI tool-call loop. Co-exists with the scheduled digest.
"""

import asyncio
import logging

from telegram import Update
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


def _is_authorized(user_id: int | None) -> bool:
    if not TELEGRAM_AUTHORIZED_USERS:
        return False
    return user_id in TELEGRAM_AUTHORIZED_USERS


async def _send_chunked(update: Update, text: str) -> None:
    if not text:
        text = "(empty reply)"
    for i in range(0, len(text), CHUNK_LIMIT):
        await update.effective_chat.send_message(text[i:i + CHUNK_LIMIT])


async def _refuse(update: Update) -> None:
    user = update.effective_user
    log.warning("Unauthorized Q&A attempt from user_id=%s username=%s",
                user.id if user else None, user.username if user else None)
    await update.effective_chat.send_message(
        "Sorry — this bot only answers its configured admin."
    )


async def cmd_runnow(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id if update.effective_user else None):
        await _refuse(update)
        return
    await update.effective_chat.send_message("Running a full digest now…")
    # Import here to avoid a circular import (main.py imports bot).
    from main import run_agent
    await asyncio.to_thread(run_agent)
    await update.effective_chat.send_message("Digest sent.")


async def on_text(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
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


def build_application() -> Application | None:
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set; bot disabled")
        return None
    if not TELEGRAM_AUTHORIZED_USERS:
        log.info("TELEGRAM_AUTHORIZED_USERS empty; Q&A bot disabled (digest still active)")
        return None

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("runnow", cmd_runnow))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
