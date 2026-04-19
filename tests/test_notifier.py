"""Tests for yad2_watcher.notifier"""

from __future__ import annotations

import requests

from yad2_watcher.fetcher import Listing
from yad2_watcher.notifier import TelegramNotifier, _format_message

# ---------------------------------------------------------------------------
# _format_message
# ---------------------------------------------------------------------------


class TestFormatMessage:
    def test_contains_token_url(self, private_listing: Listing) -> None:
        msg = _format_message(private_listing)
        assert "abc123" in msg
        assert "yad2.co.il/item/abc123" in msg

    def test_contains_price(self, private_listing: Listing) -> None:
        msg = _format_message(private_listing)
        assert "7,500" in msg or "7500" in msg

    def test_contains_neighborhood_from_search_name(self, private_listing: Listing) -> None:
        msg = _format_message(private_listing)
        assert "גבעת הורדים" in msg

    def test_falls_back_to_listing_neighborhood(self, private_listing: Listing) -> None:
        private_listing.search_neighborhood_name = ""
        msg = _format_message(private_listing)
        assert "גבעת הורדים" in msg  # from listing.neighborhood

    def test_falls_back_to_unknown_neighborhood(self, minimal_listing: Listing) -> None:
        minimal_listing.search_neighborhood_name = ""
        msg = _format_message(minimal_listing)
        assert "שכונה לא ידועה" in msg

    def test_integer_rooms_displayed_without_decimal(self, private_listing: Listing) -> None:
        private_listing.rooms = 4.0
        msg = _format_message(private_listing)
        assert "4 חדרים" in msg
        assert "4.0 חדרים" not in msg

    def test_fractional_rooms_displayed_with_decimal(self, agency_listing: Listing) -> None:
        agency_listing.rooms = 4.5
        msg = _format_message(agency_listing)
        assert "4.5 חדרים" in msg

    def test_ground_floor_label(self, agency_listing: Listing) -> None:
        agency_listing.floor = 0
        msg = _format_message(agency_listing)
        assert "קרקע" in msg

    def test_upper_floor_label(self, private_listing: Listing) -> None:
        private_listing.floor = 3
        msg = _format_message(private_listing)
        assert "קומה 3" in msg

    def test_no_floor_info_when_none(self, minimal_listing: Listing) -> None:
        msg = _format_message(minimal_listing)
        assert "קומה" not in msg
        assert "קרקע" not in msg

    def test_tags_included_up_to_3(self, private_listing: Listing) -> None:
        # Use ASCII strings that cannot appear in the Hebrew message body
        private_listing.tags = ["parking", "balcony", "storage", "elevator"]
        msg = _format_message(private_listing)
        assert "parking" in msg
        assert "storage" in msg
        assert "elevator" not in msg  # 4th tag truncated

    def test_private_ad_type_label(self, private_listing: Listing) -> None:
        msg = _format_message(private_listing)
        assert "פרטי" in msg

    def test_agency_ad_type_label(self, agency_listing: Listing) -> None:
        msg = _format_message(agency_listing)
        assert "תיווך" in msg

    def test_no_sqm_section_when_none(self, minimal_listing: Listing) -> None:
        msg = _format_message(minimal_listing)
        assert "מ״ר" not in msg


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------


def _ok_response(mocker):
    resp = mocker.MagicMock()
    resp.raise_for_status = mocker.MagicMock()
    resp.json.return_value = {"ok": True}
    return resp


def _fail_response(mocker):
    resp = mocker.MagicMock()
    resp.raise_for_status = mocker.MagicMock()
    resp.json.return_value = {"ok": False, "description": "Forbidden"}
    return resp


class TestSendText:
    def test_sends_to_all_chat_ids(self, mocker, private_listing: Listing) -> None:
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111", "222", "333"])
        result = notifier.send_text("hello")
        assert result is True
        assert post.call_count == 3
        # Each call targets a different chat_id
        chat_ids_used = [c.kwargs["json"]["chat_id"] for c in post.call_args_list]
        assert set(chat_ids_used) == {"111", "222", "333"}

    def test_returns_false_if_any_chat_fails(self, mocker) -> None:
        responses = [_ok_response(mocker), _fail_response(mocker)]
        mocker.patch("yad2_watcher.notifier.requests.post", side_effect=responses)
        notifier = TelegramNotifier("TOKEN", ["111", "222"])
        assert notifier.send_text("hello") is False

    def test_returns_false_on_network_error(self, mocker) -> None:
        mocker.patch(
            "yad2_watcher.notifier.requests.post",
            side_effect=requests.ConnectionError("unreachable"),
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        assert notifier.send_text("hello") is False

    def test_empty_chat_ids_returns_true(self, mocker) -> None:
        # all() on empty iterable is True
        mocker.patch("yad2_watcher.notifier.requests.post")
        notifier = TelegramNotifier("TOKEN", [])
        assert notifier.send_text("hello") is True


class TestSendListing:
    def test_sends_markdown_text(self, mocker, private_listing: Listing) -> None:
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        notifier.send_listing(private_listing)
        payload = post.call_args.kwargs["json"]
        assert payload["parse_mode"] == "Markdown"
        assert "abc123" in payload["text"]

    def test_web_preview_disabled_for_text_mode(self, mocker, private_listing: Listing) -> None:
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        notifier.send_listing(private_listing)
        payload = post.call_args.kwargs["json"]
        assert payload["disable_web_page_preview"] is False


class TestSendPhoto:
    def test_sends_photo_when_cover_image_present(self, mocker, private_listing: Listing) -> None:
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        notifier.send_photo(private_listing)
        url = post.call_args[0][0]
        assert "sendPhoto" in url

    def test_falls_back_to_text_when_no_cover(self, mocker, agency_listing: Listing) -> None:
        """agency_listing has no cover_image — must fall back to send_listing."""
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        notifier.send_photo(agency_listing)
        url = post.call_args[0][0]
        assert "sendMessage" in url

    def test_falls_back_to_text_on_photo_api_failure(
        self, mocker, private_listing: Listing
    ) -> None:
        responses = [_fail_response(mocker), _ok_response(mocker)]
        post = mocker.patch("yad2_watcher.notifier.requests.post", side_effect=responses)
        notifier = TelegramNotifier("TOKEN", ["111"])
        result = notifier.send_photo(private_listing)
        # First call: sendPhoto (failed), second call: sendMessage (fallback)
        assert post.call_count == 2
        fallback_url = post.call_args_list[1][0][0]
        assert "sendMessage" in fallback_url
        assert result is True

    def test_falls_back_to_text_on_photo_network_error(
        self, mocker, private_listing: Listing
    ) -> None:
        responses = [
            requests.ConnectionError("network"),
            _ok_response(mocker),
        ]
        post = mocker.patch("yad2_watcher.notifier.requests.post", side_effect=responses)
        notifier = TelegramNotifier("TOKEN", ["111"])
        result = notifier.send_photo(private_listing)
        assert result is True
        assert post.call_count == 2

    def test_caption_truncated_at_1024_chars(self, mocker, private_listing: Listing) -> None:
        private_listing.tags = ["x" * 300] * 3  # force a very long tag
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111"])
        notifier.send_photo(private_listing)
        payload = post.call_args.kwargs["json"]
        assert len(payload["caption"]) <= 1024

    def test_broadcasts_to_multiple_chats(self, mocker, private_listing: Listing) -> None:
        post = mocker.patch(
            "yad2_watcher.notifier.requests.post", return_value=_ok_response(mocker)
        )
        notifier = TelegramNotifier("TOKEN", ["111", "222"])
        notifier.send_photo(private_listing)
        assert post.call_count == 2


class TestGetChatId:
    def test_extracts_chat_from_updates(self, mocker) -> None:
        updates = {
            "ok": True,
            "result": [
                {
                    "message": {
                        "chat": {
                            "id": 12345,
                            "first_name": "Ilia",
                            "last_name": "Test",
                            "username": "ilia_t",
                            "type": "private",
                        }
                    }
                }
            ],
        }
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = updates
        mock_resp.raise_for_status = mocker.MagicMock()
        mocker.patch("yad2_watcher.notifier.requests.get", return_value=mock_resp)

        chats = TelegramNotifier.get_chat_id("TOKEN")
        assert len(chats) == 1
        assert chats[0]["chat_id"] == "12345"
        assert "Ilia" in chats[0]["name"]

    def test_returns_empty_on_no_updates(self, mocker) -> None:
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": []}
        mock_resp.raise_for_status = mocker.MagicMock()
        mocker.patch("yad2_watcher.notifier.requests.get", return_value=mock_resp)
        assert TelegramNotifier.get_chat_id("TOKEN") == []
