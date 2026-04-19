"""Tests for yad2_watcher.journal"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from yad2_watcher.journal import Journal, NullJournal

from .conftest import RAW_MINIMAL, RAW_PRIVATE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TZ_IL = timezone(timedelta(hours=3))  # UTC+3 (Israel Standard Time)


def _ts(year: int, month: int, day: int, hour: int = 22, minute: int = 31) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=TZ_IL)


def _make_listing(token: str = "abc123", nbhd_name: str = "rasko"):
    from yad2_watcher.fetcher import _parse_listing

    raw = {**RAW_PRIVATE, "token": token}
    listing = _parse_listing(raw, "private")
    listing.search_neighborhood_id = 561
    listing.search_neighborhood_name = nbhd_name
    return listing


def _make_minimal_listing():
    from yad2_watcher.fetcher import _parse_listing

    listing = _parse_listing(RAW_MINIMAL, "private")
    listing.search_neighborhood_name = ""
    return listing


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJournalFileCreation:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        """First write should create the monthly file."""
        journal = Journal(tmp_path)
        listing = _make_listing()
        journal.append(listing, ts=_ts(2026, 4, 19))

        expected = tmp_path / "journal_2026-04.md"
        assert expected.exists()

    def test_append_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Journal should create nested directories if they don't exist."""
        journal_dir = tmp_path / "a" / "b" / "c"
        journal = Journal(journal_dir)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19))
        assert (journal_dir / "journal_2026-04.md").exists()


class TestJournalEntryFormat:
    def test_heading_contains_price(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "₪7,500" in content

    def test_heading_contains_timestamp(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19, 22, 31))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "2026-04-19 22:31" in content

    def test_heading_contains_neighborhood_tag(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(nbhd_name="rasko"), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "#neighborhood/rasko" in content

    def test_entry_contains_url(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(token="abc123"), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "https://www.yad2.co.il/item/abc123" in content

    def test_entry_contains_address(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        # RAW_PRIVATE has street "דוד שמעוני"
        assert "דוד שמעוני" in content

    def test_entry_contains_rooms_and_sqm(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "**Rooms:** 4" in content
        assert "**SQM:** 90" in content

    def test_entry_contains_floor(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(), ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "**Floor:** 2" in content

    def test_heading_omits_tag_when_no_neighborhood(self, tmp_path: Path) -> None:
        """If search_neighborhood_name is empty, no #neighborhood tag in heading."""
        journal = Journal(tmp_path)
        listing = _make_minimal_listing()
        journal.append(listing, ts=_ts(2026, 4, 19))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert "#neighborhood/" not in content

    def test_multiple_appends_accumulate(self, tmp_path: Path) -> None:
        """Two appends to the same month should both appear in the file."""
        journal = Journal(tmp_path)
        journal.append(_make_listing(token="tok1"), ts=_ts(2026, 4, 19))
        journal.append(_make_listing(token="tok2"), ts=_ts(2026, 4, 20))
        content = (tmp_path / "journal_2026-04.md").read_text(encoding="utf-8")
        assert content.count("## ") == 2


class TestMonthlyRotation:
    def test_different_months_go_to_different_files(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(token="tok1"), ts=_ts(2026, 4, 19))
        journal.append(_make_listing(token="tok2"), ts=_ts(2026, 5, 1))

        assert (tmp_path / "journal_2026-04.md").exists()
        assert (tmp_path / "journal_2026-05.md").exists()

    def test_same_month_same_file(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_make_listing(token="tok1"), ts=_ts(2026, 4, 1))
        journal.append(_make_listing(token="tok2"), ts=_ts(2026, 4, 30))

        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1


class TestNullJournal:
    def test_null_journal_writes_nothing(self, tmp_path: Path) -> None:
        """NullJournal must not create any files."""
        null = NullJournal()
        null.append(_make_listing())
        assert list(tmp_path.glob("*.md")) == []

    def test_null_journal_accepts_listing(self) -> None:
        """NullJournal.append should not raise."""
        null = NullJournal()
        null.append(_make_listing())  # no exception


class TestWatcherJournalIntegration:
    """Verify the Watcher calls journal.append for each new listing."""

    def _make_config(self, tmp_path: Path) -> dict:
        return {
            "telegram": {"bot_token": "FAKE", "chat_ids": ["111"]},
            "watcher": {
                "db_path": str(tmp_path / "test.db"),
                "request_timeout": 5,
                "fetch_delay_seconds": 0,
                "journal_enabled": True,
                "journal_path": str(tmp_path / "journal"),
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
                {"id": 561, "name": "rasko", "url_slug": "jerusalem-area"},
            ],
        }

    def test_journal_append_called_for_each_new_listing(self, mocker, tmp_path: Path) -> None:
        from yad2_watcher.watcher import Watcher

        listings = [_make_listing("tok1"), _make_listing("tok2")]
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=listings)

        with Watcher(self._make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=True)
            append_spy = mocker.patch.object(w._journal, "append")
            w.run_once()

        assert append_spy.call_count == 2

    def test_journal_not_called_for_seen_listings(self, mocker, tmp_path: Path) -> None:
        from yad2_watcher.watcher import Watcher

        listing = _make_listing("tok1")
        mocker.patch("yad2_watcher.watcher.fetch_listings", return_value=[listing])

        with Watcher(self._make_config(tmp_path)) as w:
            w._notifier.send_photo = mocker.MagicMock(return_value=True)
            w.run_once()  # marks tok1 as seen
            append_spy = mocker.patch.object(w._journal, "append")
            w.run_once()  # tok1 already seen — no append

        append_spy.assert_not_called()

    def test_null_journal_when_disabled(self, mocker, tmp_path: Path) -> None:
        from yad2_watcher.journal import NullJournal
        from yad2_watcher.watcher import Watcher

        config = self._make_config(tmp_path)
        config["watcher"]["journal_enabled"] = False

        with Watcher(config) as w:
            assert isinstance(w._journal, NullJournal)
