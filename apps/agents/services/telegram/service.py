from __future__ import annotations

import logging
import re

import httpx
from django.conf import settings

from apps.agents.metrics import (
    telegram_notification_failures_total,
    telegram_notifications_sent_total,
)
from apps.agents.models import TelegramConfig

logger = logging.getLogger(__name__)


def _sanitize_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in escape_chars:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


def _sanitize_markdown_v2(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in escape_chars:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


class TelegramService:
    def __init__(self, config: TelegramConfig | None = None):
        self._config = config
        self._client = httpx.Client(timeout=15.0)

    @property
    def is_available(self) -> bool:
        return self._config is not None and bool(self._config.bot_token) and bool(self._config.chat_id)

    def send_message(self, text: str, parse_mode: str | None = None) -> bool:
        if not self.is_available:
            logger.warning("Telegram not configured, skipping message")
            return False

        assert self._config is not None

        safe_text = text
        if parse_mode == "Markdown":
            safe_text = _sanitize_markdown(text)
        elif parse_mode == "MarkdownV2":
            safe_text = _sanitize_markdown_v2(text)

        payload: dict = {
            "chat_id": self._config.chat_id,
            "text": safe_text[:4096],
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = self._client.post(
                f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage",
                json=payload,
            )
            if resp.status_code == 200:
                telegram_notifications_sent_total.labels(status="ok").inc()
                logger.info("Telegram message sent successfully")
                return True
            else:
                telegram_notification_failures_total.labels(error_type=f"http_{resp.status_code}").inc()
                logger.warning("Telegram API error: %s %s", resp.status_code, resp.text)
                return False
        except httpx.RequestError as exc:
            telegram_notification_failures_total.labels(error_type="network").inc()
            logger.error("Telegram send failed: %s", exc)
            return False

    def send_digest(self, text: str) -> bool:
        chunk_size = 4000
        if len(text) <= chunk_size:
            return self._send_trusted_markdown(text)

        success = True
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            header = f"[Part {i // chunk_size + 1}/{(len(text) + chunk_size - 1) // chunk_size}]\n\n"
            if i > 0:
                chunk = header + chunk
            if not self._send_trusted_markdown(chunk):
                success = False
        return success

    def _send_trusted_markdown(self, text: str) -> bool:
        """Send text with markdown formatting, no sanitization (trusted content only)."""
        if not self.is_available:
            logger.warning("Telegram not configured, skipping message")
            return False

        assert self._config is not None

        payload: dict = {
            "chat_id": self._config.chat_id,
            "text": text[:4096],
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._client.post(
                f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage",
                json=payload,
            )
            if resp.status_code == 200:
                telegram_notifications_sent_total.labels(status="ok").inc()
                logger.info("Telegram digest sent successfully")
                return True
            if resp.status_code == 400:
                payload.pop("parse_mode", None)
                resp = self._client.post(
                    f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage",
                    json=payload,
                )
                if resp.status_code == 200:
                    telegram_notifications_sent_total.labels(status="ok").inc()
                    logger.info("Telegram digest sent (plain text fallback)")
                    return True
            telegram_notification_failures_total.labels(error_type=f"http_{resp.status_code}").inc()
            logger.warning("Telegram digest error: %s %s", resp.status_code, resp.text)
            return False
        except httpx.RequestError as exc:
            telegram_notification_failures_total.labels(error_type="network").inc()
            logger.error("Telegram digest send failed: %s", exc)
            return False

    def send_error(self, error_message: str, agent_name: str = "") -> bool:
        text = f"⚠️ *Agent Error*"
        if agent_name:
            text += f": {agent_name}"
        text += f"\n\n`{error_message[:500]}`"
        return self.send_message(text, parse_mode="Markdown")
