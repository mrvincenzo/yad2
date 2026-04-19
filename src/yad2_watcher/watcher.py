"""
watcher.py — Core orchestration: fetch → diff → notify for all neighborhoods.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .fetcher import Listing, fetch_listings
from .notifier import TelegramNotifier
from .store import SeenStore

logger = logging.getLogger(__name__)


class Watcher:
    """
    Runs a single pass across all configured neighborhoods:
    fetch listings → find new ones → send Telegram alerts → mark as seen.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        telegram_cfg = config["telegram"]
        self._notifier = TelegramNotifier(
            bot_token=telegram_cfg["bot_token"],
            chat_id=str(telegram_cfg["chat_id"]),
        )
        watcher_cfg = config.get("watcher", {})
        self._db_path = watcher_cfg.get("db_path", "~/.yad2_watcher/seen.db")
        self._timeout = watcher_cfg.get("request_timeout", 20)
        self._fetch_delay = watcher_cfg.get("fetch_delay_seconds", 5)
        self._store = SeenStore(self._db_path)

        defaults = config.get("search_defaults", {})
        self._min_price = defaults.get("min_price", 6000)
        self._max_price = defaults.get("max_price", 9000)
        self._min_rooms = defaults.get("min_rooms", 3.5)
        self._max_rooms = defaults.get("max_rooms", 4.5)
        self._area = defaults.get("area", 7)
        self._city = defaults.get("city", 3000)

        self._neighborhoods = config.get("neighborhoods", [])

    def run_once(self) -> dict[str, int]:
        """
        Execute one full scan across all neighborhoods.
        Returns a summary dict: {neighborhood_name: new_listing_count}.
        """
        summary: dict[str, int] = {}

        for i, nbhd in enumerate(self._neighborhoods):
            nbhd_id = nbhd["id"]
            nbhd_name = nbhd["name"]
            url_slug = nbhd.get("url_slug", "jerusalem-area")

            logger.info("Fetching neighborhood %s (%s)...", nbhd_name, nbhd_id)

            try:
                listings = fetch_listings(
                    url_slug=url_slug,
                    neighborhood_id=nbhd_id,
                    neighborhood_name=nbhd_name,
                    min_price=self._min_price,
                    max_price=self._max_price,
                    min_rooms=self._min_rooms,
                    max_rooms=self._max_rooms,
                    area=self._area,
                    city=self._city,
                    timeout=self._timeout,
                )
            except Exception as exc:
                logger.error(
                    "Failed to fetch %s (%s): %s", nbhd_name, nbhd_id, exc
                )
                self._store.log_run(nbhd_id, 0, 0, str(exc))
                summary[nbhd_name] = 0
                continue

            new_listings = self._find_new(listings)
            logger.info(
                "  %s: %d fetched, %d new",
                nbhd_name,
                len(listings),
                len(new_listings),
            )

            for listing in new_listings:
                self._send_and_mark(listing)

            self._store.log_run(nbhd_id, len(listings), len(new_listings))
            summary[nbhd_name] = len(new_listings)

            # Polite delay between neighborhoods (except last)
            if i < len(self._neighborhoods) - 1:
                time.sleep(self._fetch_delay)

        return summary

    def _find_new(self, listings: list[Listing]) -> list[Listing]:
        """Filter to only listings not yet seen."""
        return [l for l in listings if not self._store.is_seen(l.token)]

    def _send_and_mark(self, listing: Listing) -> None:
        """Send Telegram alert and mark as seen (even if send fails, to avoid spam)."""
        try:
            success = self._notifier.send_photo(listing)
            if success:
                logger.info("  ✓ Sent alert for token %s (%s)", listing.token, listing.url)
            else:
                logger.warning("  ✗ Failed to send alert for token %s", listing.token)
        except Exception as exc:
            logger.error("  ✗ Error sending alert for %s: %s", listing.token, exc)
        finally:
            # Always mark as seen to prevent infinite re-alerting on send failures
            self._store.mark_seen(listing.token, listing.search_neighborhood_id, listing.price)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "Watcher":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
