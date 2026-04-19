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

    # Header
    neighborhood_display = listing.search_neighborhood_name or listing.neighborhood or "שכונה לא ידועה"
    lines.append(f"🏠 *דירה חדשה | {neighborhood_display}*")
    lines.append("")

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
    """Sends listing alerts via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    def _api_url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self._token, method=method)

    def send_listing(self, listing: Listing) -> bool:
        """
        Send a formatted Telegram message for a listing.
        Returns True on success, False on failure (logs the error).
        """
        text = _format_message(listing)
        return self._send_message(text, disable_web_page_preview=False)

    def send_text(self, text: str) -> bool:
        """Send a plain text message (used for status/startup alerts)."""
        return self._send_message(text, disable_web_page_preview=True)

    def _send_message(self, text: str, *, disable_web_page_preview: bool = True) -> bool:
        payload = {
            "chat_id": self._chat_id,
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
        Send a listing as a photo message with caption.
        Falls back to text-only if no cover image.
        """
        if not listing.cover_image:
            return self.send_listing(listing)

        caption = _format_message(listing)
        # Telegram captions have a 1024 char limit
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        payload = {
            "chat_id": self._chat_id,
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
                # Cover image URL may be expired — fall back to text
                logger.warning(
                    "sendPhoto failed (%s), falling back to text", result.get("description")
                )
                return self.send_listing(listing)
            return True
        except requests.RequestException as exc:
            logger.error("Failed to send Telegram photo: %s", exc)
            return self.send_listing(listing)

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
                        "name": chat.get("first_name", "")
                        + " "
                        + chat.get("last_name", ""),
                        "username": chat.get("username", ""),
                        "type": chat.get("type", ""),
                    }
                )
        return chats
