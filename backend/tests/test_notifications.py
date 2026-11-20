from backend.config import Environment
from backend.notifications import Notifier


def test_notification_toggle_blocks_all_delivery(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled notifications attempted network delivery")

    monkeypatch.setattr("backend.notifications.httpx.post", forbidden)
    notifier = Notifier(Environment(telegram_bot_token="token", telegram_chat_id="chat"))
    notifier.send("Subject", "Message", enabled=False)


def test_notification_failure_does_not_escape(monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("test delivery failure")

    monkeypatch.setattr("backend.notifications.httpx.post", failed)
    notifier = Notifier(Environment(telegram_bot_token="token", telegram_chat_id="chat"))
    notifier.send("Subject", "Message", enabled=True)
