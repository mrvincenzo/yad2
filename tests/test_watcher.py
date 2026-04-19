"""Tests for yad2_watcher.watcher"""

from __future__ import annotations

from yad2_watcher.fetcher import Listing
from yad2_watcher.watcher import Watcher

from .conftest import RAW_PRIVATE


def _make_config(tmp_path, chat_ids=None) -> dict:
    return {
        "telegram": {
            "bot_token": "FAKE_TOKEN",
            "chat_ids": chat_ids or ["111"],
        },
        "watcher": {
            "db_path": str(tmp_path / "test.db"),
            "request_timeout": 5,
            "fetch_delay_seconds": 0,  # no sleep in tests
            "interval_minutes": 30,
        },
        "search_defaults": {
            "min_price": 6000,
            "max_price": 9000,
            "min_rooms": 4.0,
            "max_rooms": 4.5,
            "area": 7,
            "city": 3000,
        },
        "neighborhoods": [
            {"id": 561, "name": "גבעת הורדים", "url_slug": "jerusalem-area"},
            {"id": 544, "name": "קטמון", "url_slug": "jerusalem-area"},
        ],
    }


def _make_listing(token: str, nbhd_id: int = 561) -> Listing:
    from yad2_watcher.fetcher import _parse_listing

    raw = {**RAW_PRIVATE, "token": token}
    listing = _parse_listing(raw, "private")
    listing.search_neighborhood_id = nbhd_id
    listing.search_neighborhood_name = "test"
    return listing


class TestRunOnce:
    def test_new_listings_are_sent(self, mocker, tmp_path) -> None:
        listings = [_make_listing("tok1"), _make_listing("tok2")]
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=listings)
        with Watcher(_make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=True)
            summary = w.run_once()
        # tok1+tok2 are new in neighborhood 561 (2 calls), then already seen in 544 (0 calls)
        assert w._notifier.send_photo.call_count == 2
        assert summary["גבעת הורדים"] == 2
        assert summary["קטמון"] == 0

    def test_already_seen_listings_not_resent(self, mocker, tmp_path) -> None:
        listing = _make_listing("tok1")
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=[listing])

        with Watcher(_make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=True)
            # First run — new
            summary1 = w.run_once()
            # Second run — already seen
            summary2 = w.run_once()

        assert summary1["גבעת הורדים"] == 1
        assert summary2["גבעת הורדים"] == 0

    def test_fetch_error_recorded_and_skipped(self, mocker, tmp_path) -> None:
        mocker.patch(
            "yad2_watcher.watcher.fetch_listings",
            side_effect=ValueError("network error"),
        )
        with Watcher(_make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=True)
            summary = w.run_once()

        # Both neighborhoods failed — 0 new each
        assert all(v == 0 for v in summary.values())

    def test_send_failure_still_marks_seen(self, mocker, tmp_path) -> None:
        """Even if Telegram send fails, the token is marked seen to prevent spam."""
        listing = _make_listing("tok1")
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=[listing])

        with Watcher(_make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=False)
            w.run_once()
            # Second pass — should not try to send again
            send_count_before = w._notifier.send_photo.call_count
            w.run_once()
            send_count_after = w._notifier.send_photo.call_count

        assert send_count_before == send_count_after  # no new call in second pass

    def test_summary_has_entry_per_neighborhood(self, mocker, tmp_path) -> None:
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=[])
        with Watcher(_make_config(tmp_path)) as w:
            summary = w.run_once()
        assert set(summary.keys()) == {"גבעת הורדים", "קטמון"}

    def test_multiple_chat_ids_used(self, mocker, tmp_path) -> None:
        # Use unique tokens per neighborhood so both are "new"
        def fetch_side_effect(url_slug, neighborhood_id, neighborhood_name, **kw):
            return [_make_listing(f"tok_{neighborhood_id}", neighborhood_id)]

        mocker.patch("yad2_watcher.watcher.fetch_listings", side_effect=fetch_side_effect)

        with Watcher(_make_config(tmp_path, chat_ids=["111", "222"])) as w:
            mock_send = mocker.patch.object(w._notifier, "send_photo", return_value=True)
            w.run_once()

        # One new listing per neighborhood = 2 send_photo calls total
        assert mock_send.call_count == 2


class TestFindNew:
    def test_filters_seen_tokens(self, tmp_path) -> None:
        config = _make_config(tmp_path)
        with Watcher(config) as w:
            w._store.mark_seen("tok1", 561, 7500)
            listings = [_make_listing("tok1"), _make_listing("tok2")]
            new = w._find_new(listings)

        assert len(new) == 1
        assert new[0].token == "tok2"

    def test_all_new_when_store_empty(self, tmp_path) -> None:
        with Watcher(_make_config(tmp_path)) as w:
            listings = [_make_listing("a"), _make_listing("b")]
            assert len(w._find_new(listings)) == 2

    def test_all_seen_returns_empty(self, tmp_path) -> None:
        with Watcher(_make_config(tmp_path)) as w:
            w._store.mark_seen("tok1", 561, 7500)
            listings = [_make_listing("tok1")]
            assert w._find_new(listings) == []


class TestContextManager:
    def test_watcher_closes_store_on_exit(self, mocker, tmp_path) -> None:
        config = _make_config(tmp_path)
        with Watcher(config) as w:
            close_spy = mocker.spy(w._store, "close")
        close_spy.assert_called_once()
