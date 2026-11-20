"""Best-effort Telegram and email alerts; failures never interrupt trading."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import Environment, get_env

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, env: Environment | None = None):
        self.env = env or get_env()

    def send(self, subject: str, message: str, enabled: bool = True) -> None:
        """Deliver only when the persisted operator alert toggle allows it."""
        if not enabled:
            return
        errors = []
        if self.env.telegram_bot_token and self.env.telegram_chat_id:
            try:
                response = httpx.post(
                    f"https://api.telegram.org/bot{self.env.telegram_bot_token}/sendMessage",
                    json={"chat_id": self.env.telegram_chat_id, "text": f"{subject}\n{message}"}, timeout=8,
                )
                response.raise_for_status()
            except Exception as exc:
                errors.append(f"Telegram: {exc}")
        if self.env.smtp_host and self.env.alert_email:
            try:
                email = EmailMessage()
                email["Subject"] = subject
                email["From"] = self.env.smtp_username or "apex@localhost"
                email["To"] = self.env.alert_email
                email.set_content(message)
                with smtplib.SMTP(self.env.smtp_host, self.env.smtp_port, timeout=8) as smtp:
                    smtp.starttls()
                    if self.env.smtp_username:
                        smtp.login(self.env.smtp_username, self.env.smtp_password)
                    smtp.send_message(email)
            except Exception as exc:
                errors.append(f"Email: {exc}")
        for error in errors:
            logger.error("Notification delivery failed: %s", error)
