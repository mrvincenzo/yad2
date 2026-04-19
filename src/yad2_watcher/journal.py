"""
journal.py — Append new apartment listings to a rolling monthly Markdown journal.

Each new listing is written as a ## section to a per-month file:
    ~/.yad2/journal/journal_2026-04.md

Entries include an Obsidian-native #neighborhood/<name> tag on the heading line
so listings can be filtered by neighbourhood across all monthly files.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .fetcher import Listing

logger = logging.getLogger(__name__)


class Journal:
    """
    Appends new listing entries to monthly Markdown files.

    Thread-safe for single-process use (append-mode writes are atomic on POSIX).
    """

    def __init__(self, journal_dir: str | Path) -> None:
        self._dir = Path(journal_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _monthly_path(self, ts: datetime) -> Path:
        return self._dir / f"journal_{ts.strftime('%Y-%m')}.md"

    def append(self, listing: Listing, *, ts: datetime | None = None) -> None:
        """
        Append a formatted entry for *listing* to the current month's journal file.

        Args:
            listing: The new listing to record.
            ts: Timestamp override (used in tests). Defaults to now (local time).
        """
        now = ts or datetime.now().astimezone()
        path = self._monthly_path(now)
        entry = self._format_entry(listing, now)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(entry)
            logger.debug("Journal: wrote entry for token %s to %s", listing.token, path)
        except OSError as exc:
            logger.error("Journal: failed to write entry for %s: %s", listing.token, exc)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_price(price: int) -> str:
        return f"₪{price:,}".replace(",", ",")

    @staticmethod
    def _format_rooms(rooms: float | None) -> str:
        if rooms is None:
            return "—"
        return str(int(rooms)) if rooms == int(rooms) else str(rooms)

    def _format_entry(self, listing: Listing, now: datetime) -> str:
        """Build the full Markdown block for a single listing."""
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        price_str = self._format_price(listing.price)
        neighborhood_tag = (
            f"#neighborhood/{listing.search_neighborhood_name}"
            if listing.search_neighborhood_name
            else ""
        )

        # Heading line: timestamp | price | #neighborhood/tag
        heading_parts = [timestamp, f"{price_str}/חודש"]
        if neighborhood_tag:
            heading_parts.append(neighborhood_tag)
        heading = "## " + " | ".join(heading_parts)

        lines = [heading, ""]

        # Address
        lines.append(f"- 📍 **Address:** {listing.address_text}")

        # Details
        detail_parts: list[str] = []
        detail_parts.append(f"**Rooms:** {self._format_rooms(listing.rooms)}")
        if listing.sqm is not None:
            detail_parts.append(f"**SQM:** {listing.sqm}")
        if listing.floor is not None:
            floor_label = "קרקע" if listing.floor == 0 else str(listing.floor)
            detail_parts.append(f"**Floor:** {floor_label}")
        lines.append("- 🛏️ " + " | ".join(detail_parts))

        # Ad type + tags
        from .notifier import _AD_TYPE_LABELS  # noqa: PLC0415

        ad_label = _AD_TYPE_LABELS.get(listing.ad_type, listing.ad_type)
        type_parts = [ad_label] + list(listing.tags[:3])
        lines.append("- 🏷️ **Type:** " + " · ".join(type_parts))

        # Link
        lines.append(f"- 🔗 [פתח מודעה]({listing.url})")

        # Timestamp footer
        lines.append(f"- ⏰ **Seen at:** {now.strftime('%Y-%m-%d %H:%M:%S')}")

        lines.append("")  # blank line separating entries
        lines.append("")

        return "\n".join(lines)


class NullJournal:
    """A no-op journal used when journaling is disabled in config."""

    def append(self, listing: Listing, **_: object) -> None:  # noqa: ARG002
        pass
