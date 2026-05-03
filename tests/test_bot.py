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
    """Every COMMAND_QUERIES entry should appear in BOT_COMMAND_MENU and vice
    versa for the user-facing commands. /help and /runnow are handled
    directly and don't need entries in COMMAND_QUERIES."""
    menu_names = {c.command for c in bot.BOT_COMMAND_MENU}
    query_names = set(bot.COMMAND_QUERIES.keys())
    direct_handlers = {"help", "runnow"}
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
