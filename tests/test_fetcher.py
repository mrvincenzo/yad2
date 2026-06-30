"""Tests for yad2_watcher.fetcher"""

from __future__ import annotations

import json

import pytest
from curl_cffi import requests  # type: ignore[import-untyped]

from yad2_watcher.fetcher import (
    CaptchaBlockError,
    Listing,
    _parse_listing,
    fetch_item_customer,
    fetch_listings,
)

from .conftest import RAW_AGENCY, RAW_MINIMAL, RAW_PRIVATE, make_feed_response, make_next_data_html

# ---------------------------------------------------------------------------
# Listing dataclass
# ---------------------------------------------------------------------------


class TestListingProperties:
    def test_url(self, private_listing: Listing) -> None:
        assert private_listing.url == "https://www.yad2.co.il/item/abc123"

    def test_address_text_full(self, private_listing: Listing) -> None:
        assert private_listing.address_text == "דוד שמעוני 10 (גבעת הורדים)"

    def test_address_text_no_street(self, agency_listing: Listing) -> None:
        # agency listing has street but no house number stripped; let's use minimal
        from yad2_watcher.fetcher import _parse_listing

        raw = {**RAW_MINIMAL, "address": {"neighborhood": {"text": "רחביה"}}}
        listing = _parse_listing(raw, "private")
        assert listing.address_text == "(רחביה)"

    def test_address_text_fallback(self, minimal_listing: Listing) -> None:
        assert minimal_listing.address_text == "כתובת לא ידועה"

    def test_address_text_street_only(self) -> None:
        from yad2_watcher.fetcher import _parse_listing

        raw = {**RAW_MINIMAL, "address": {"street": {"text": "הרצל"}}}
        listing = _parse_listing(raw, "private")
        assert listing.address_text == "הרצל"


# ---------------------------------------------------------------------------
# _parse_listing
# ---------------------------------------------------------------------------


class TestParseListing:
    def test_full_private(self) -> None:
        listing = _parse_listing(RAW_PRIVATE, "private")
        assert listing.token == "abc123"
        assert listing.price == 7500
        assert listing.ad_type == "private"
        assert listing.rooms == 4
        assert listing.sqm == 90
        assert listing.floor == 2
        assert listing.street == "דוד שמעוני"
        assert listing.house_number == 10
        assert listing.neighborhood == "גבעת הורדים"
        assert listing.city == "ירושלים"
        assert listing.cover_image == "https://img.yad2.co.il/sample.jpeg"
        assert listing.tags == ["3 מרפסות", "חניה"]

    def test_agency_ground_floor(self) -> None:
        listing = _parse_listing(RAW_AGENCY, "agency")
        assert listing.floor == 0
        assert listing.rooms == 4.5
        assert listing.cover_image is None
        assert listing.tags == []

    def test_minimal_listing(self) -> None:
        listing = _parse_listing(RAW_MINIMAL, "private")
        assert listing.token == "min001"
        assert listing.price == 6000
        assert listing.rooms is None
        assert listing.sqm is None
        assert listing.floor is None
        assert listing.street is None
        assert listing.neighborhood is None

    def test_tags_filtered_empty_name(self) -> None:
        raw = {**RAW_PRIVATE, "tags": [{"name": ""}, {"name": "חניה", "id": 1}]}
        listing = _parse_listing(raw, "private")
        assert listing.tags == ["חניה"]

    def test_missing_token_raises(self) -> None:
        raw = {**RAW_PRIVATE}
        del raw["token"]
        with pytest.raises(KeyError):
            _parse_listing(raw, "private")

    def test_default_price_zero(self) -> None:
        raw = {**RAW_MINIMAL}
        del raw["price"]
        listing = _parse_listing(raw, "private")
        assert listing.price == 0


# ---------------------------------------------------------------------------
# fetch_listings (all HTTP calls are mocked)
# ---------------------------------------------------------------------------


class TestFetchListings:
    """Tests for fetch_listings — now uses the gw.yad2.co.il JSON feed API."""

    def _make_resp(self, mocker, feed_data: dict, content_type: str = "application/json"):
        """Build a mock HTTP response returning JSON feed data."""
        resp = mocker.MagicMock()
        resp.raise_for_status = mocker.MagicMock()
        resp.headers = {"content-type": content_type}
        resp.json.return_value = feed_data
        resp.text = ""  # used only for CAPTCHA detection on non-JSON responses
        return resp

    def _call(self, mocker, feed_data: dict, **kwargs):
        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._make_resp(mocker, feed_data),
        )
        defaults = dict(
            url_slug="jerusalem-area",
            neighborhood_id=561,
            neighborhood_name="גבעת הורדים",
            min_price=6000,
            max_price=9000,
            min_rooms=4.0,
            max_rooms=4.5,
            area=7,
            city=3000,
        )
        defaults.update(kwargs)
        return fetch_listings(**defaults)

    def test_returns_private_and_agency(self, mocker) -> None:
        feed = make_feed_response(private=[RAW_PRIVATE], agency=[RAW_AGENCY])
        listings = self._call(mocker, feed)
        assert len(listings) == 2
        tokens = {l.token for l in listings}
        assert "abc123" in tokens
        assert "xyz789" in tokens

    def test_sets_neighborhood_metadata(self, mocker) -> None:
        feed = make_feed_response(private=[RAW_PRIVATE])
        listings = self._call(mocker, feed)
        assert listings[0].search_neighborhood_id == 561
        assert listings[0].search_neighborhood_name == "גבעת הורדים"

    def test_empty_results(self, mocker) -> None:
        feed = make_feed_response()
        listings = self._call(mocker, feed)
        assert listings == []

    def test_parses_platinum_section(self, mocker) -> None:
        """Promoted listings in platinum/kingOfTheHar/etc. are included."""
        platinum_raw = {**RAW_PRIVATE, "token": "plat001", "price": 8000}
        feed = make_feed_response(platinum=[platinum_raw])
        listings = self._call(mocker, feed)
        assert len(listings) == 1
        assert listings[0].token == "plat001"

    def test_deduplicates_tokens(self, mocker) -> None:
        """Same token appearing in multiple sections is returned only once."""
        dup = {**RAW_PRIVATE, "token": "dup999"}
        feed = make_feed_response(private=[dup], agency=[dup])
        listings = self._call(mocker, feed)
        assert len(listings) == 1
        assert listings[0].token == "dup999"

    def test_skips_malformed_listing(self, mocker) -> None:
        bad = {"no_token": True}  # missing required token field
        feed = make_feed_response(private=[bad, RAW_PRIVATE])
        listings = self._call(mocker, feed)
        assert len(listings) == 1
        assert listings[0].token == "abc123"

    def test_raises_on_captcha_html_response(self, mocker) -> None:
        resp = mocker.MagicMock()
        resp.raise_for_status = mocker.MagicMock()
        resp.headers = {"content-type": "text/html"}
        resp.text = "<html><body>ShieldSquare CAPTCHA triggered</body></html>"
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=resp)
        with pytest.raises(CaptchaBlockError):
            fetch_listings(
                url_slug="jerusalem-area",
                neighborhood_id=561,
                neighborhood_name="test",
                min_price=6000,
                max_price=9000,
                min_rooms=4.0,
                max_rooms=4.5,
                area=7,
                city=3000,
            )

    def test_raises_on_non_json_non_captcha(self, mocker) -> None:
        resp = mocker.MagicMock()
        resp.raise_for_status = mocker.MagicMock()
        resp.headers = {"content-type": "text/plain"}
        resp.text = "something unexpected"
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=resp)
        with pytest.raises(CaptchaBlockError):
            fetch_listings(
                url_slug="jerusalem-area",
                neighborhood_id=561,
                neighborhood_name="test",
                min_price=6000,
                max_price=9000,
                min_rooms=4.0,
                max_rooms=4.5,
                area=7,
                city=3000,
            )

    def test_raises_on_http_error(self, mocker) -> None:
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.side_effect = requests.errors.RequestsError("403")
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=mock_resp)
        with pytest.raises(requests.errors.RequestsError):
            fetch_listings(
                url_slug="jerusalem-area",
                neighborhood_id=561,
                neighborhood_name="test",
                min_price=6000,
                max_price=9000,
                min_rooms=4.0,
                max_rooms=4.5,
                area=7,
                city=3000,
            )

    def test_calls_feed_api_url(self, mocker) -> None:
        """Verifies the call goes to the feed API, not the HTML page."""
        feed = make_feed_response()
        mock_get = mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._make_resp(mocker, feed),
        )
        fetch_listings(
            url_slug="jerusalem-area",
            neighborhood_id=561,
            neighborhood_name="test",
            min_price=6000,
            max_price=9000,
            min_rooms=4.0,
            max_rooms=4.5,
            area=7,
            city=3000,
        )
        url = mock_get.call_args[0][0]
        assert "gw.yad2.co.il/realestate-feed/rent/feed" in url
        params = mock_get.call_args[1]["params"]
        assert params["neighborhood"] == 561
        assert params["minPrice"] == 6000
        assert params["maxPrice"] == 9000
        assert params["region"] == "6"  # Jerusalem

    def test_cors_headers_present(self, mocker) -> None:
        """Verifies Origin and Referer are set (CORS-shaped request)."""
        feed = make_feed_response()
        mock_get = mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._make_resp(mocker, feed),
        )
        fetch_listings(
            url_slug="jerusalem-area",
            neighborhood_id=561,
            neighborhood_name="test",
            min_price=6000,
            max_price=9000,
            min_rooms=4.0,
            max_rooms=4.5,
            area=7,
            city=3000,
        )
        headers = mock_get.call_args[1]["headers"]
        assert headers["Origin"] == "https://www.yad2.co.il"
        assert "jerusalem-area" in headers["Referer"]
        assert headers["Sec-Fetch-Mode"] == "cors"

    def test_pagination_fetches_all_pages(self, mocker) -> None:
        """When totalPages > 1, subsequent pages are fetched (with sleep mocked)."""
        page1_listing = {**RAW_PRIVATE, "token": "p1tok"}
        page2_listing = {**RAW_PRIVATE, "token": "p2tok", "price": 8000}

        page1 = {"data": {
            "pagination": {"totalPages": 2, "total": 2},
            "private": [page1_listing], "agency": [], "platinum": [],
            "kingOfTheHar": [], "trio": [], "booster": [],
        }}
        page2 = {"data": {
            "pagination": {"totalPages": 2, "total": 2},
            "private": [page2_listing], "agency": [], "platinum": [],
            "kingOfTheHar": [], "trio": [], "booster": [],
        }}

        def make_paged_resp(data):
            r = mocker.MagicMock()
            r.raise_for_status = mocker.MagicMock()
            r.headers = {"content-type": "application/json"}
            r.json.return_value = data
            return r

        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            side_effect=[make_paged_resp(page1), make_paged_resp(page2)],
        )
        mocker.patch("yad2_watcher.fetcher.time.sleep")

        listings = fetch_listings(
            url_slug="jerusalem-area",
            neighborhood_id=561,
            neighborhood_name="test",
            min_price=6000,
            max_price=9000,
            min_rooms=4.0,
            max_rooms=4.5,
            area=7,
            city=3000,
        )
        assert len(listings) == 2
        assert {l.token for l in listings} == {"p1tok", "p2tok"}


# ---------------------------------------------------------------------------
# fetch_item_customer
# ---------------------------------------------------------------------------


class TestFetchItemCustomer:
    def _ok(self, mocker, data: dict):
        resp = mocker.MagicMock()
        resp.raise_for_status = mocker.MagicMock()
        resp.json.return_value = {"data": data, "message": "OK"}
        return resp

    def test_returns_phone_on_success(self, mocker) -> None:
        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._ok(mocker, {"name": "מאיר", "phone": "052-4283314"}),
        )
        assert fetch_item_customer("abc123") == "052-4283314"

    def test_falls_back_to_broker_phone(self, mocker) -> None:
        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._ok(mocker, {"name": "סוכן", "brokerPhone": "055-9999999"}),
        )
        assert fetch_item_customer("abc123") == "055-9999999"

    def test_returns_none_when_no_phone_field(self, mocker) -> None:
        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._ok(mocker, {"name": "מאיר", "id": 123}),
        )
        assert fetch_item_customer("abc123") is None

    def test_returns_none_on_http_error(self, mocker) -> None:
        resp = mocker.MagicMock()
        resp.raise_for_status.side_effect = requests.errors.RequestsError("404")
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=resp)
        assert fetch_item_customer("abc123") is None

    def test_returns_none_on_network_error(self, mocker) -> None:
        mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            side_effect=requests.errors.RequestsError("unreachable"),
        )
        assert fetch_item_customer("abc123") is None

    def test_calls_correct_url(self, mocker) -> None:
        mock_get = mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=self._ok(mocker, {"phone": "052-1111111"}),
        )
        fetch_item_customer("tok999")
        assert mock_get.call_args[0][0] == "https://gw.yad2.co.il/realestate-item/tok999/customer"
