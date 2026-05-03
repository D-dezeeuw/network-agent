import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
            }, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[telegram] Failed to send: {e}")
            return False
    return True


if __name__ == "__main__":
    ok = send_message("🤖 Network agent test message")
    print(f"sent: {ok}")
