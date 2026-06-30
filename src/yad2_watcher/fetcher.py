"""
fetcher.py — Fetches Yad2 listing data via the gateway feed JSON API.

Primary path: gw.yad2.co.il/realestate-feed/rent/feed
  - Returns JSON directly; no HTML parsing required.
  - Needs CORS-shaped headers (origin + referer from www.yad2.co.il).
  - Much less bot-protected than the HTML/Next.js page.

Secondary path (per-item): www.yad2.co.il/item/{token}
  - Still HTML-scraped via __NEXT_DATA__ for phone/image details.
  - curl_cffi Chrome impersonation applied here.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from curl_cffi import requests  # type: ignore[import-untyped]
from curl_cffi.requests import Session  # type: ignore[import-untyped]


class CaptchaBlockError(Exception):
    """Raised when Yad2 returns a Radware/ShieldSquare CAPTCHA page."""
    pass


# ---------------------------------------------------------------------------
# Feed API  — gw.yad2.co.il/realestate-feed/rent/feed
# Headers mimic the browser making a same-site CORS XHR from www.yad2.co.il.
# ---------------------------------------------------------------------------

FEED_BASE_URL = "https://gw.yad2.co.il/realestate-feed/rent/feed"

# url_slug → numeric region required by the feed API.
# Extend by inspecting Network tab in Chrome for other regions.
REGION_BY_SLUG: dict[str, str] = {
    "center-and-sharon": "1",
    "south": "2",
    "jerusalem-area": "6",
}

# All listing buckets the feed API may return.
_FEED_SECTIONS = ("private", "agency", "platinum", "kingOfTheHar", "trio", "booster")

# CORS-shaped headers — look like the browser's XHR, not a page load.
_FEED_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.yad2.co.il",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# ---------------------------------------------------------------------------
# HTML page headers — used only for per-item pages (fetch_item_data)
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

_CAPTCHA_MARKERS = ("shieldsquare", "radware", "validate.perfdrive.com", "hcaptcha")


@dataclass
class Listing:
    """A single apartment listing extracted from Yad2."""

    token: str
    price: int
    ad_type: str  # "private" | "agency" | section name for promoted slots
    rooms: float | None
    sqm: int | None
    floor: int | None
    street: str | None
    house_number: int | None
    neighborhood: str | None
    city: str | None
    cover_image: str | None
    tags: list[str] = field(default_factory=list)
    # Price history from the database (past prices, chronologically)
    price_history: list[int] = field(default_factory=list)
    # Which search neighborhood generated this listing
    search_neighborhood_id: int = 0
    search_neighborhood_name: str = ""
    phone: str | None = None

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
            parts.append(f"({self.neighborhood})")
        return " ".join(parts) if parts else "כתובת לא ידועה"


def _text(value: Any) -> str | None:
    """Extract a display string from a value that may be a dict or a plain string.

    The feed API returns address fields as dicts with a 'text' key
    (e.g. {"text": "ירושלים"}).  This helper handles both dict and raw-string forms.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("text") or value.get("title") or None
    s = str(value).strip()
    return s or None


def _raise_if_captcha(body: str) -> None:
    """Raise CaptchaBlockError if the response body looks like a bot-block page."""
    body_lower = body.lower()
    if any(m in body_lower for m in _CAPTCHA_MARKERS):
        raise CaptchaBlockError(
            "Yad2 returned a bot-detection page (Radware/ShieldSquare). "
            "Run 'yad2-watcher bootstrap-cookies' to refresh the session."
        )


def _parse_listing(raw: dict[str, Any], ad_type: str) -> Listing:
    """Convert a raw feed/NEXT_DATA listing dict into a Listing object."""
    addr = raw.get("address") or {}
    details = raw.get("additionalDetails") or {}
    meta = raw.get("metaData") or {}
    house = addr.get("house") or {}
    tags = [t.get("name", "") for t in raw.get("tags") or [] if t.get("name")]

    return Listing(
        token=raw["token"],
        price=raw.get("price") or 0,
        ad_type=ad_type,
        rooms=details.get("roomsCount"),
        # squareMeterBuild is the fallback the feed API sometimes uses
        sqm=details.get("squareMeter") or meta.get("squareMeterBuild"),
        floor=house.get("floor"),
        street=_text(addr.get("street")),
        house_number=house.get("number"),
        neighborhood=_text(addr.get("neighborhood")),
        city=_text(addr.get("city")),
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
    session: Session | None = None,
    timeout: int = 20,
) -> list[Listing]:
    """
    Fetch all current listings for a single neighborhood from the feed JSON API.

    Calls gw.yad2.co.il/realestate-feed/rent/feed with CORS-shaped headers.
    Handles multi-page feeds automatically (0.8-1.8 s delay between pages).
    Parses all listing sections (private, agency, platinum, kingOfTheHar, trio, booster).
    Returns deduplicated Listing objects.

    Raises CaptchaBlockError if a bot-detection page is returned.
    Raises requests.RequestException on network errors.
    Raises ValueError if the JSON structure is unexpected.
    """
    region = REGION_BY_SLUG.get(url_slug, "6")  # default Jerusalem
    display_url = (
        f"https://www.yad2.co.il/realestate/rent/{url_slug}"
        f"?minPrice={min_price}&maxPrice={max_price}"
        f"&minRooms={min_rooms}&maxRooms={max_rooms}"
        f"&area={area}&city={city}&neighborhood={neighborhood_id}"
    )
    headers = {**_FEED_HEADERS, "Referer": display_url}

    base_params: dict[str, Any] = {
        "minPrice": min_price,
        "maxPrice": max_price,
        "minRooms": min_rooms,
        "maxRooms": max_rooms,
        "area": area,
        "city": city,
        "neighborhood": neighborhood_id,
        "region": region,
    }

    _session = session or requests

    def _fetch_page(page: int) -> dict[str, Any]:
        params = {**base_params}
        if page > 1:
            params["page"] = page

        resp = _session.get(
            FEED_BASE_URL,
            params=params,
            headers=headers,
            impersonate="chrome",
            timeout=timeout,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "application/json" not in content_type:
            # Got HTML instead of JSON — likely a CAPTCHA interstitial
            _raise_if_captcha(resp.text)
            raise CaptchaBlockError(
                f"Yad2 feed API returned non-JSON content ({content_type}). "
                "Run 'yad2-watcher bootstrap-cookies' to refresh the session."
            )

        try:
            return resp.json()
        except Exception as exc:
            raise ValueError(
                f"Failed to parse feed API JSON: {exc} | body: {resp.text[:300]}"
            ) from exc

    # Page 1 — also reads pagination metadata
    data = _fetch_page(1)

    try:
        feed = data["data"]
        pagination = feed.get("pagination") or {}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unexpected feed API response structure: {exc}") from exc

    total_pages = int(pagination.get("totalPages") or 1)
    MAX_PAGES = 10  # safety cap

    seen_tokens: set[str] = set()
    listings: list[Listing] = []

    def _collect(feed_data: dict[str, Any]) -> None:
        for section in _FEED_SECTIONS:
            for raw in feed_data.get(section) or []:
                try:
                    ad_type = raw.get("adType") or raw.get("advertiserType") or section
                    listing = _parse_listing(raw, ad_type)
                    listing.search_neighborhood_id = neighborhood_id
                    listing.search_neighborhood_name = neighborhood_name
                    if listing.token not in seen_tokens:
                        seen_tokens.add(listing.token)
                        listings.append(listing)
                except (KeyError, TypeError):
                    continue  # skip malformed individual listings

    _collect(feed)

    for page in range(2, min(total_pages, MAX_PAGES) + 1):
        time.sleep(random.uniform(0.8, 1.8))
        try:
            next_data = _fetch_page(page)
            _collect(next_data["data"])
        except (KeyError, TypeError):
            break

    return listings


# ---------------------------------------------------------------------------
# Gateway API — phone number lookup (unchanged)
# ---------------------------------------------------------------------------

_GW_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": _HEADERS["User-Agent"],
    "Sec-Ch-Ua": _HEADERS["Sec-Ch-Ua"],
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def fetch_item_customer(token: str, session: Session | None = None, timeout: int = 20) -> str | None:
    """
    Fetch the seller phone number for a listing via the Yad2 gateway API.

    Returns a phone string (e.g. "052-4283314") or None if unavailable.
    Does not raise — failures are silent so a missing phone never blocks alerting.
    """
    url = f"https://gw.yad2.co.il/realestate-item/{token}/customer"
    headers = {**_GW_HEADERS, "Referer": f"https://www.yad2.co.il/item/{token}"}
    _session = session or requests
    try:
        resp = _session.get(url, headers=headers, impersonate="chrome", timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("phone") or data.get("brokerPhone") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-item HTML page — still used by download / send-listing commands
# ---------------------------------------------------------------------------

def fetch_item_data(token: str, session: Session | None = None, timeout: int = 20) -> dict[str, Any]:
    """
    Fetch raw item details and photos by listing token from the HTML item page.

    Returns the parsed dictionary of the 'item' query from __NEXT_DATA__.
    Raises requests.RequestException on network errors.
    Raises ValueError if structure is unexpected or CAPTCHA is hit.
    """
    url = f"https://www.yad2.co.il/item/{token}"
    _session = session or requests
    response = _session.get(url, headers=_HEADERS, impersonate="chrome", timeout=timeout)
    response.raise_for_status()

    html = response.text
    _raise_if_captcha(html)

    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError("Could not find __NEXT_DATA__ in the Yad2 response.")

    raw_data = json.loads(match.group(1))

    try:
        queries = raw_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unexpected __NEXT_DATA__ structure: {exc}") from exc

    for query in queries:
        key = query.get("queryKey", [])
        if isinstance(key, list) and len(key) >= 2 and key[0] == "item" and key[1] == token:
            return query.get("state", {}).get("data", {})

    raise ValueError(f"Could not find 'item' query for token '{token}' in __NEXT_DATA__.")


def fetch_single_listing(token: str, *, timeout: int = 20) -> Listing:
    """Fetch and parse a single listing by token from its item page."""
    raw = fetch_item_data(token, timeout=timeout)
    ad_type = raw.get("adType") or raw.get("advertiserType") or "private"
    return _parse_listing(raw, ad_type)
