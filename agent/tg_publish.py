import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

CHUNK_LIMIT = 4096
INTER_MESSAGE_DELAY_S = 0.4


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


def _post_send(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[telegram] Failed to send: {e}")
        return False


def send_message(text: str) -> bool:
    """Send a single message, splitting at CHUNK_LIMIT char boundaries."""
    if not text:
        return True
    chunks = [text[i:i + CHUNK_LIMIT] for i in range(0, len(text), CHUNK_LIMIT)]
    success = True
    for chunk in chunks:
        if not _post_send(chunk):
            success = False
    return success


def send_messages(parts: list[str]) -> bool:
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
        if not send_message(part):
            success = False
    return success


if __name__ == "__main__":
    ok = send_message("🤖 <b>Network agent</b> test message")
    print(f"sent: {ok}")
