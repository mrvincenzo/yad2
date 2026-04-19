"""
notifier.py — Sends Telegram messages for new Yad2 listings.

Uses the Telegram Bot API directly (no third-party library needed).
Messages are formatted with Hebrew-friendly emojis and direct links.
"""

from __future__ import annotations

import logging

import requests

from .fetcher import Listing

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Ad type display labels
_AD_TYPE_LABELS = {
    "private": "פרטי",
    "agency": "תיווך",
}


def _format_message(listing: Listing) -> str:
    """Build a clean Telegram message for a single listing."""
    lines = []

    # Price
    price_str = f"₪{listing.price:,}".replace(",", ",")
    lines.append(f"💰 {price_str}/חודש")

    # Details row
    detail_parts = []
    if listing.rooms is not None:
        rooms_display = int(listing.rooms) if listing.rooms == int(listing.rooms) else listing.rooms
        detail_parts.append(f"{rooms_display} חדרים")
    if listing.sqm:
        detail_parts.append(f"{listing.sqm} מ״ר")
    if listing.floor is not None:
        floor_label = "קרקע" if listing.floor == 0 else f"קומה {listing.floor}"
        detail_parts.append(floor_label)
    if detail_parts:
        lines.append("🛏️ " + " | ".join(detail_parts))

    # Address
    lines.append(f"📍 {listing.address_text}")

    # Ad type + tags
    ad_label = _AD_TYPE_LABELS.get(listing.ad_type, listing.ad_type)
    tag_line = ad_label
    if listing.tags:
        tag_line += " · " + " · ".join(listing.tags[:3])  # cap at 3 tags
    lines.append(f"🏷️ {tag_line}")

    # Link
    lines.append("")
    lines.append(f"🔗 [פתח מודעה]({listing.url})")

    return "\n".join(lines)


class TelegramNotifier:
    """Sends listing alerts to one or more Telegram chats via Bot API."""

    def __init__(self, bot_token: str, chat_ids: list[str]) -> None:
        self._token = bot_token
        self._chat_ids = chat_ids

    def _api_url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self._token, method=method)

    def send_listing(self, listing: Listing) -> bool:
        """Send a formatted message for a listing to all configured chats."""
        text = _format_message(listing)
        return all(
            self._send_message(cid, text, disable_web_page_preview=False) for cid in self._chat_ids
        )

    def send_text(self, text: str) -> bool:
        """Send a plain text message to all configured chats."""
        return all(
            self._send_message(cid, text, disable_web_page_preview=True) for cid in self._chat_ids
        )

    def _send_message(
        self, chat_id: str, text: str, *, disable_web_page_preview: bool = True
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": disable_web_page_preview,
        }
        try:
            resp = requests.post(
                self._api_url("sendMessage"),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                logger.error("Telegram API error: %s", result)
                return False
            return True
        except requests.RequestException as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    def send_photo(self, listing: Listing) -> bool:
        """
        Send a listing as a photo message with caption to all configured chats.
        Falls back to text-only if no cover image.
        """
        if not listing.cover_image:
            return self.send_listing(listing)
        return all(self._send_photo_to(cid, listing) for cid in self._chat_ids)

    def _send_photo_to(self, chat_id: str, listing: Listing) -> bool:
        """Send photo+caption to a single chat_id."""
        caption = _format_message(listing)
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        payload = {
            "chat_id": chat_id,
            "photo": listing.cover_image,
            "caption": caption,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(
                self._api_url("sendPhoto"),
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                logger.warning(
                    "sendPhoto failed for chat %s (%s), falling back to text",
                    chat_id,
                    result.get("description"),
                )
                return self._send_message(
                    chat_id, _format_message(listing), disable_web_page_preview=False
                )
            return True
        except requests.RequestException as exc:
            logger.error("Failed to send Telegram photo to %s: %s", chat_id, exc)
            return self._send_message(
                chat_id, _format_message(listing), disable_web_page_preview=False
            )

    @classmethod
    def get_chat_id(cls, bot_token: str) -> list[dict]:
        """
        Return recent updates from the bot to find your chat_id.
        User must have sent the bot at least one message first.
        """
        url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        updates = data.get("result", [])
        chats = []
        for update in updates:
            msg = update.get("message", {})
            chat = msg.get("chat", {})
            if chat:
                chats.append(
                    {
                        "chat_id": str(chat.get("id")),
                        "name": chat.get("first_name", "") + " " + chat.get("last_name", ""),
                        "username": chat.get("username", ""),
                        "type": chat.get("type", ""),
                    }
                )
        return chats
