"""Tests for yad2_watcher.fetcher"""

from __future__ import annotations

import json

import pytest
import requests

from yad2_watcher.fetcher import Listing, _parse_listing, fetch_listings

from .conftest import RAW_AGENCY, RAW_MINIMAL, RAW_PRIVATE, make_next_data_html

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
    def _call(self, mocker, html: str, **kwargs):
        mock_resp = mocker.MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = mocker.MagicMock()
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=mock_resp)
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
        html = make_next_data_html(private=[RAW_PRIVATE], agency=[RAW_AGENCY])
        listings = self._call(mocker, html)
        assert len(listings) == 2
        assert listings[0].ad_type == "private"
        assert listings[1].ad_type == "agency"

    def test_sets_neighborhood_metadata(self, mocker) -> None:
        html = make_next_data_html(private=[RAW_PRIVATE])
        listings = self._call(mocker, html)
        assert listings[0].search_neighborhood_id == 561
        assert listings[0].search_neighborhood_name == "גבעת הורדים"

    def test_empty_results(self, mocker) -> None:
        html = make_next_data_html()
        listings = self._call(mocker, html)
        assert listings == []

    def test_skips_malformed_listing(self, mocker) -> None:
        bad = {"no_token": True}  # missing token field
        html = make_next_data_html(private=[bad, RAW_PRIVATE])
        listings = self._call(mocker, html)
        assert len(listings) == 1
        assert listings[0].token == "abc123"

    def test_raises_on_captcha(self, mocker) -> None:
        html = "<html><body>ShieldSquare CAPTCHA triggered</body></html>"
        with pytest.raises(ValueError, match="ShieldSquare"):
            self._call(mocker, html)

    def test_raises_on_missing_next_data(self, mocker) -> None:
        html = "<html><body>no script tag here</body></html>"
        with pytest.raises(ValueError, match="__NEXT_DATA__"):
            self._call(mocker, html)

    def test_raises_on_missing_feed_query(self, mocker) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [{"queryKey": ["some-other-query"], "state": {"data": {}}}]
                    }
                }
            }
        }
        html = (
            "<html><body>"
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
            "</body></html>"
        )
        with pytest.raises(ValueError, match="realestate-rent-feed"):
            self._call(mocker, html)

    def test_raises_on_http_error(self, mocker) -> None:
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
        mocker.patch("yad2_watcher.fetcher.requests.get", return_value=mock_resp)
        with pytest.raises(requests.HTTPError):
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

    def test_correct_url_and_params(self, mocker) -> None:
        html = make_next_data_html()
        mock_get = mocker.patch(
            "yad2_watcher.fetcher.requests.get",
            return_value=mocker.MagicMock(text=html, raise_for_status=mocker.MagicMock()),
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
        call_args = mock_get.call_args
        assert "jerusalem-area" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["neighborhood"] == 561
        assert params["minPrice"] == 6000
        assert params["maxPrice"] == 9000
