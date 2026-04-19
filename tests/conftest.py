"""Shared pytest fixtures for yad2-watcher tests."""

from __future__ import annotations

import json

import pytest

from yad2_watcher.fetcher import Listing

# ---------------------------------------------------------------------------
# Sample raw listing dicts (as they appear in __NEXT_DATA__)
# ---------------------------------------------------------------------------

RAW_PRIVATE = {
    "token": "abc123",
    "price": 7500,
    "address": {
        "region": {"text": "ירושלים והסביבה", "id": 6},
        "city": {"text": "ירושלים"},
        "area": {"text": "אזור ירושלים"},
        "neighborhood": {"text": "גבעת הורדים"},
        "street": {"text": "דוד שמעוני"},
        "house": {"number": 10, "floor": 2},
        "coords": {"lon": 35.2, "lat": 31.76},
    },
    "subcategoryId": 2,
    "categoryId": 2,
    "adType": "private",
    "additionalDetails": {
        "property": {"text": "דירה"},
        "roomsCount": 4,
        "squareMeter": 90,
    },
    "metaData": {
        "coverImage": "https://img.yad2.co.il/sample.jpeg",
        "images": ["https://img.yad2.co.il/sample.jpeg"],
    },
    "tags": [
        {"name": "3 מרפסות", "id": 1013, "priority": 1},
        {"name": "חניה", "id": 1001, "priority": 2},
    ],
    "orderId": 12345,
    "priority": 2,
}

RAW_AGENCY = {
    "token": "xyz789",
    "price": 8500,
    "address": {
        "city": {"text": "ירושלים"},
        "neighborhood": {"text": "קטמון"},
        "street": {"text": "עזה"},
        "house": {"number": 5, "floor": 0},
    },
    "adType": "agency",
    "additionalDetails": {
        "roomsCount": 4.5,
        "squareMeter": 100,
    },
    "metaData": {},
    "tags": [],
    "orderId": 67890,
    "priority": 1,
}

RAW_MINIMAL = {
    "token": "min001",
    "price": 6000,
    "address": {},
    "adType": "private",
    "additionalDetails": {},
    "metaData": {},
    "tags": [],
}


@pytest.fixture
def private_listing() -> Listing:
    """A fully-populated private listing."""
    from yad2_watcher.fetcher import _parse_listing

    listing = _parse_listing(RAW_PRIVATE, "private")
    listing.search_neighborhood_id = 561
    listing.search_neighborhood_name = "גבעת הורדים"
    return listing


@pytest.fixture
def agency_listing() -> Listing:
    """An agency listing with floor=0 (ground floor)."""
    from yad2_watcher.fetcher import _parse_listing

    listing = _parse_listing(RAW_AGENCY, "agency")
    listing.search_neighborhood_id = 544
    listing.search_neighborhood_name = "קטמון"
    return listing


@pytest.fixture
def minimal_listing() -> Listing:
    """A listing with only the bare minimum fields."""
    from yad2_watcher.fetcher import _parse_listing

    return _parse_listing(RAW_MINIMAL, "private")


def make_next_data_html(private: list | None = None, agency: list | None = None) -> str:
    """Build a minimal HTML page with __NEXT_DATA__ containing listing data."""
    feed_data = {
        "private": private or [],
        "agency": agency or [],
        "pagination": {"total": len(private or []) + len(agency or []), "totalPages": 1},
    }
    next_data = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": [
                                "realestate-rent-feed",
                                {"city": "3000", "neighborhood": "561"},
                            ],
                            "state": {"data": feed_data},
                        }
                    ]
                }
            }
        }
    }
    return (
        "<html><head></head><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        "</body></html>"
    )
