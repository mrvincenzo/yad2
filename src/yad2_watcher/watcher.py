"""
watcher.py — Core orchestration: fetch → diff → notify for all neighborhoods.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

from curl_cffi.requests import Session  # type: ignore[import-untyped]

from .fetcher import CaptchaBlockError, Listing, fetch_item_customer, fetch_listings
from .journal import Journal, NullJournal
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
        chat_ids = [str(c) for c in telegram_cfg.get("chat_ids", [])]
        error_chat_ids = [str(c) for c in telegram_cfg.get("error_chat_ids", [])]
        self._notifier = TelegramNotifier(
            bot_token=telegram_cfg["bot_token"],
            chat_ids=chat_ids,
            error_chat_ids=error_chat_ids,
        )
        watcher_cfg = config.get("watcher", {})
        self._db_path = watcher_cfg.get("db_path", "~/.yad2_watcher/seen.db")
        self._timeout = watcher_cfg.get("request_timeout", 20)
        self._fetch_delay = watcher_cfg.get("fetch_delay_seconds", 5)
        self._store = SeenStore(self._db_path)

        journal_enabled = watcher_cfg.get("journal_enabled", True)
        journal_path = watcher_cfg.get("journal_path", "~/.yad2/journal")
        self._journal: Journal | NullJournal = (
            Journal(journal_path) if journal_enabled else NullJournal()
        )

        defaults = config.get("search_defaults", {})
        self._min_price = defaults.get("min_price", 6000)
        self._max_price = defaults.get("max_price", 9000)
        self._min_rooms = defaults.get("min_rooms", 3.5)
        self._max_rooms = defaults.get("max_rooms", 4.5)
        self._area = defaults.get("area", 7)
        self._city = defaults.get("city", 3000)

        self._neighborhoods = config.get("neighborhoods", [])

        # Persistent cookie store — makes us look like a returning browser to
        # ShieldSquare rather than a fresh bot session every hour.
        cookies_path_raw = watcher_cfg.get("cookies_path", "~/.yad2_watcher/cookies.json")
        self._cookies_path = Path(os.path.expanduser(cookies_path_raw))
        self._session = self._build_session()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _build_session(self) -> Session:
        """Create a curl_cffi Session and pre-load persisted cookies."""
        session = Session(impersonate="chrome")
        if self._cookies_path.exists():
            try:
                raw = json.loads(self._cookies_path.read_text())
                for name, value in raw.items():
                    session.cookies.set(name, value)  # type: ignore[attr-defined]
                logger.debug("Loaded %d cookies from %s", len(raw), self._cookies_path)
            except Exception as exc:
                logger.warning("Could not load cookies from %s: %s", self._cookies_path, exc)
        return session

    def _save_cookies(self) -> None:
        """Persist current session cookies to disk for the next run."""
        try:
            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            cookies = dict(self._session.cookies)  # type: ignore[attr-defined]
            self._cookies_path.write_text(json.dumps(cookies))
            logger.debug("Saved %d cookies to %s", len(cookies), self._cookies_path)
        except Exception as exc:
            logger.warning("Could not save cookies to %s: %s", self._cookies_path, exc)

    def run_once(self) -> dict[str, int]:
        """
        Execute one full scan across all neighborhoods.
        Returns a summary dict: {neighborhood_name: new_listing_count}.
        """
        summary: dict[str, int] = {}

        # Brief random pre-flight pause — simulates page load, not a robot.
        time.sleep(random.uniform(0.5, 2.5))

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
                    session=self._session,
                    timeout=self._timeout,
                )
            except CaptchaBlockError as exc:
                logger.error("Scan aborted at %s (%s) due to CAPTCHA block.", nbhd_name, nbhd_id)
                self._store.log_run(nbhd_id, 0, 0, "CAPTCHA block")
                self._notifier.send_error(
                    f"🛑 *Scan Aborted*\nHit a CAPTCHA block while fetching {nbhd_name} ({nbhd_id})."
                )
                break
            except Exception as exc:
                logger.error("Failed to fetch %s (%s): %s", nbhd_name, nbhd_id, exc)
                self._store.log_run(nbhd_id, 0, 0, str(exc))
                self._notifier.send_error(f"Failed to fetch {nbhd_name} ({nbhd_id}): `{exc}`")
                summary[nbhd_name] = 0
                continue

            new_listings = self._find_new_or_updated(listings)
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

            # Polite delay between neighborhoods (except last), with ±50% jitter.
            if i < len(self._neighborhoods) - 1:
                jitter = random.uniform(0.5, 1.5)
                time.sleep(self._fetch_delay * jitter)

        self._save_cookies()
        return summary

    def _find_new_or_updated(self, listings: list[Listing]) -> list[Listing]:
        """Filter to only brand new listings or ones with updated prices."""
        result = []
        for listing in listings:
            history = self._store.get_price_history(listing.token)
            if history is None:
                # Brand new
                listing.price_history = []
                result.append(listing)
            elif history and history[-1] != listing.price:
                # Price changed
                listing.price_history = history
                result.append(listing)
        return result

    def _send_and_mark(self, listing: Listing) -> None:
        """Send Telegram alert, journal, and mark as seen (even if send fails)."""
        listing.phone = fetch_item_customer(listing.token, session=self._session, timeout=self._timeout)
        try:
            success = self._notifier.send_photo(listing)
            if success:
                logger.info("  ✓ Sent alert for token %s (%s)", listing.token, listing.url)
            else:
                logger.warning("  ✗ Failed to send alert for token %s", listing.token)
                self._notifier.send_error(
                    f"Failed to send alert for listing {listing.token} ({listing.url})"
                )
        except Exception as exc:
            logger.error("  ✗ Error sending alert for %s: %s", listing.token, exc)
            self._notifier.send_error(
                f"Error sending alert for listing {listing.token} ({listing.url}): `{exc}`"
            )
        finally:
            # Always mark as seen and journal — prevents re-alerting on send failures
            self._store.mark_seen(listing.token, listing.search_neighborhood_id, listing.price)
            self._journal.append(listing)

    def close(self) -> None:
        self._session.close()  # type: ignore[attr-defined]
        self._store.close()

    def __enter__(self) -> Watcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
