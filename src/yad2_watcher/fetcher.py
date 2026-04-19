"""
fetcher.py — Fetches Yad2 search results by parsing the __NEXT_DATA__ JSON
embedded in the server-side-rendered Next.js page.

No Playwright, no headless browser, no proxy needed.
Standard browser request headers are sufficient (verified April 2026).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Request headers that mimic a real Chrome browser on macOS
# These are required — bare curl gets ShieldSquare CAPTCHA, these do not.
# ---------------------------------------------------------------------------
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",  # noqa: E501
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

BASE_URL = "https://www.yad2.co.il/realestate/rent/{slug}"


@dataclass
class Listing:
    """A single apartment listing extracted from Yad2."""

    token: str
    price: int
    ad_type: str  # "private" | "agency"
    rooms: float | None
    sqm: int | None
    floor: int | None
    street: str | None
    house_number: int | None
    neighborhood: str | None
    city: str | None
    cover_image: str | None
    tags: list[str] = field(default_factory=list)
    # Which search neighborhood generated this listing
    search_neighborhood_id: int = 0
    search_neighborhood_name: str = ""

    @property
    def url(self) -> str:
        return f"https://www.yad2.co.il/item/{self.token}"

    @property
    def address_text(self) -> str:
        parts = []
        if self.street:
            parts.append(self.street)
        if self.house_number:
            parts.append(str(self.house_number))
        if self.neighborhood:
            parts.append(f"{self.neighborhood}")
        return " ".join(parts) if parts else "כתובת לא ידועה"


def _parse_listing(raw: dict[str, Any], ad_type: str) -> Listing:
    """Convert a raw __NEXT_DATA__ listing dict into a Listing object."""
    addr = raw.get("address", {})
    details = raw.get("additionalDetails", {})
    meta = raw.get("metaData", {})
    house = addr.get("house", {})
    tags = [t.get("name", "") for t in raw.get("tags", []) if t.get("name")]

    return Listing(
        token=raw["token"],
        price=raw.get("price", 0),
        ad_type=ad_type,
        rooms=details.get("roomsCount"),
        sqm=details.get("squareMeter"),
        floor=house.get("floor"),
        street=addr.get("street", {}).get("text"),
        house_number=house.get("number"),
        neighborhood=addr.get("neighborhood", {}).get("text"),
        city=addr.get("city", {}).get("text"),
        cover_image=meta.get("coverImage"),
        tags=tags,
    )


def fetch_listings(
    url_slug: str,
    neighborhood_id: int,
    neighborhood_name: str,
    *,
    min_price: int,
    max_price: int,
    min_rooms: float,
    max_rooms: float,
    area: int,
    city: int,
    timeout: int = 20,
) -> list[Listing]:
    """
    Fetch all current listings for a single neighborhood search.

    Returns a list of Listing objects (private + agency, excludes promoted/platinum).
    Raises requests.RequestException on network errors.
    Raises ValueError if the page structure is unexpected (Yad2 changed format).
    """
    params = {
        "minPrice": min_price,
        "maxPrice": max_price,
        "minRooms": min_rooms,
        "maxRooms": max_rooms,
        "area": area,
        "city": city,
        "neighborhood": neighborhood_id,
    }

    url = BASE_URL.format(slug=url_slug)
    response = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()

    html = response.text

    # Sanity check — if we hit the CAPTCHA page, surface it clearly
    if "ShieldSquare" in html or "shieldsquare" in html.lower():
        raise ValueError(
            "Yad2 returned a ShieldSquare CAPTCHA. The request headers may need to be updated."
        )

    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError(
            "Could not find __NEXT_DATA__ in the Yad2 response. "
            "The page structure may have changed."
        )

    raw_data = json.loads(match.group(1))

    # Navigate to the feed query
    try:
        queries = raw_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unexpected __NEXT_DATA__ structure: {exc}") from exc

    feed_data = None
    for query in queries:
        key = query.get("queryKey", [])
        if isinstance(key, list) and len(key) > 0 and key[0] == "realestate-rent-feed":
            feed_data = query.get("state", {}).get("data", {})
            break

    if feed_data is None:
        raise ValueError(
            "Could not find 'realestate-rent-feed' query in __NEXT_DATA__. "
            "The page structure may have changed."
        )

    listings: list[Listing] = []

    for ad_type in ("private", "agency"):
        for raw in feed_data.get(ad_type, []):
            try:
                listing = _parse_listing(raw, ad_type)
                listing.search_neighborhood_id = neighborhood_id
                listing.search_neighborhood_name = neighborhood_name
                listings.append(listing)
            except (KeyError, TypeError):
                # Skip malformed individual listings
                continue

    return listings
