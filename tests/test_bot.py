import bot


def test_authorized_user_passes(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123, 456])
    assert bot._is_authorized(123) is True
    assert bot._is_authorized(456) is True


def test_unknown_user_rejected(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    assert bot._is_authorized(999) is False


def test_empty_whitelist_rejects_everything(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [])
    assert bot._is_authorized(123) is False
    assert bot._is_authorized(None) is False


def test_none_user_id_rejected(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    assert bot._is_authorized(None) is False


def test_command_queries_match_bot_command_menu():
    """Every BOT_COMMAND_MENU entry must either be a COMMAND_QUERIES key
    (routed through the AI) or a direct cmd_X handler in bot.py."""
    menu_names = {c.command for c in bot.BOT_COMMAND_MENU}
    query_names = set(bot.COMMAND_QUERIES.keys())
    direct_handlers = {
        "help", "runnow", "acks", "unsnooze", "trend", "chart", "logs",
        "mute_all", "unmute_all", "mute", "unmute",
        "set", "unset", "config", "preview", "clearmemory",
    }
    assert query_names | direct_handlers == menu_names, (
        f"menu/handler drift: only-in-menu={menu_names - (query_names | direct_handlers)}, "
        f"only-in-handlers={(query_names | direct_handlers) - menu_names}"
    )


def test_help_text_uses_html_tags_only():
    """Help text should not leak markdown asterisks (we're on HTML parse mode)."""
    assert "*" not in bot.HELP_TEXT
    assert "<b>" in bot.HELP_TEXT


def test_command_queries_are_non_empty():
    for cmd, query in bot.COMMAND_QUERIES.items():
        assert query.strip(), f"empty query for /{cmd}"


# --- _is_authorized_update: combined user+chat authorization -----------------

class _FakeUpdate:
    """Minimal stand-in for telegram.Update that exposes effective_user and
    effective_chat without dragging in PTB internals during tests."""
    def __init__(self, user_id=None, chat_id=None):
        self.effective_user = type("U", (), {"id": user_id})() if user_id else None
        self.effective_chat = type("C", (), {"id": chat_id})() if chat_id else None


def test_authorized_update_passes_for_known_user(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", -100999)
    assert bot._is_authorized_update(_FakeUpdate(user_id=123, chat_id=999)) is True


def test_authorized_update_passes_for_message_in_digest_chat(monkeypatch):
    """Channel posts have no effective_user — auth must succeed via chat_id."""
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", -1003973618569)
    assert bot._is_authorized_update(_FakeUpdate(user_id=None, chat_id=-1003973618569)) is True


def test_authorized_update_passes_when_known_user_in_digest_chat(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", -100999)
    assert bot._is_authorized_update(_FakeUpdate(user_id=123, chat_id=-100999)) is True


def test_authorized_update_rejects_unknown_user_in_other_chat(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", -100999)
    assert bot._is_authorized_update(_FakeUpdate(user_id=456, chat_id=-100888)) is False


def test_authorized_update_rejects_when_no_user_no_matching_chat(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", -100999)
    assert bot._is_authorized_update(_FakeUpdate(user_id=None, chat_id=-100888)) is False


def test_authorized_update_rejects_when_digest_chat_unset(monkeypatch):
    """Without a configured digest chat, channel posts can't authenticate at all."""
    monkeypatch.setattr(bot, "TELEGRAM_AUTHORIZED_USERS", [123])
    monkeypatch.setattr(bot, "_DIGEST_CHAT_ID", None)
    assert bot._is_authorized_update(_FakeUpdate(user_id=None, chat_id=-100999)) is False


def test_resolve_digest_chat_id_handles_non_numeric(monkeypatch):
    """If TELEGRAM_CHAT_ID is malformed, resolution returns None instead of crashing."""
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "not-a-number")
    assert bot._resolve_digest_chat_id() is None


def test_resolve_digest_chat_id_returns_int(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", "-1003973618569")
    assert bot._resolve_digest_chat_id() == -1003973618569


def test_resolve_digest_chat_id_returns_none_when_unset(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_CHAT_ID", None)
    assert bot._resolve_digest_chat_id() is None
