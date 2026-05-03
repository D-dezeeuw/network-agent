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
