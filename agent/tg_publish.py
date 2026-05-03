import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

CHUNK_LIMIT = 4096
INTER_MESSAGE_DELAY_S = 0.4

ChatId = str | int | None


def html_escape(text: str) -> str:
    """Escape user-supplied text for safe inclusion in Telegram HTML.

    Only escapes the characters Telegram cares about: &, <, >. Quotes
    are not escaped because Telegram doesn't parse them in HTML mode.
    """
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _resolve_chat(chat_id: ChatId) -> str | int | None:
    return chat_id if chat_id is not None else TELEGRAM_CHAT_ID


def _post_send(text: str, reply_markup: dict | None = None,
               chat_id: ChatId = None) -> bool:
    target = _resolve_chat(chat_id)
    if not target:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[telegram] Failed to send: {e}")
        return False


def send_message(text: str, chat_id: ChatId = None) -> bool:
    """Send a single message, splitting at CHUNK_LIMIT char boundaries.

    `chat_id` overrides the default TELEGRAM_CHAT_ID destination — used
    by the critical-chat routing in notifications.py.
    """
    if not text:
        return True
    chunks = [text[i:i + CHUNK_LIMIT] for i in range(0, len(text), CHUNK_LIMIT)]
    success = True
    for chunk in chunks:
        if not _post_send(chunk, chat_id=chat_id):
            success = False
    return success


def send_photo(image_bytes: bytes, caption: str = "", chat_id: ChatId = None) -> bool:
    """Upload a PNG (or other image) to the configured chat.

    Caption supports HTML formatting and is capped at 1024 chars (Telegram).
    Anything longer should go as a separate text message.
    """
    if not image_bytes:
        return True
    target = _resolve_chat(chat_id)
    if not target:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {"chat_id": target, "parse_mode": "HTML"}
    if caption:
        data["caption"] = caption[:1024]
    try:
        r = requests.post(
            url,
            data=data,
            files={"photo": ("chart.png", image_bytes, "image/png")},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[telegram] Failed to send photo: {e}")
        return False


def send_message_with_buttons(text: str, buttons: list[list[tuple[str, str]]],
                              chat_id: ChatId = None) -> bool:
    """Send a single message with an inline keyboard.

    `buttons` is a list of rows; each row is a list of (label, callback_data)
    tuples. callback_data must be <=64 bytes per Telegram's limit.
    """
    inline_keyboard = [
        [{"text": label, "callback_data": cb} for label, cb in row]
        for row in buttons
    ]
    return _post_send(text[:CHUNK_LIMIT],
                      reply_markup={"inline_keyboard": inline_keyboard},
                      chat_id=chat_id)


def send_messages(parts: list[str], chat_id: ChatId = None) -> bool:
    """Send several messages in order with a small inter-message delay.

    The delay keeps Telegram's flood control happy and preserves visual
    ordering in the client.
    """
    success = True
    for i, part in enumerate(parts):
        if not part:
            continue
        if i > 0:
            time.sleep(INTER_MESSAGE_DELAY_S)
        if not send_message(part, chat_id=chat_id):
            success = False
    return success


if __name__ == "__main__":
    ok = send_message("🤖 <b>Network agent</b> test message")
    print(f"sent: {ok}")
