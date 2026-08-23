"""Parsers for Swiggy order history.

The live API returns history formatted for DISPLAY, not for machines. Every field below
was captured from a real `get_food_orders` / `get_orders` response::

    orderTotal:   "₹310"                                            — string, with symbol
    orderedItems: "Veg Noodles (1),Malai Chaap (1),Cold Coffee (1)"  — one joined string
    orderedTime:  "August 22, 10:13 PM"                             — NO YEAR

Consequences the rest of the app lives with: there is no per-item price, so only
order-level totals are trustworthy; and the exact instant of a food order is unknowable,
so we keep a date plus the raw string.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ParsedItem",
    "ParsedOrder",
    "parse_inr",
    "parse_item_list",
    "parse_ordered_on",
    "parse_food_order",
    "parse_instamart_order",
    "item_key",
]


@dataclass(frozen=True)
class ParsedItem:
    name: str
    quantity: int = 1


@dataclass(frozen=True)
class ParsedOrder:
    source: str  # "food" | "instamart"
    external_id: str
    ordered_on: dt.date | None
    ordered_time_raw: str | None
    total_inr: int | None
    status: str | None
    merchant: str | None
    items: list[ParsedItem] = field(default_factory=list)


def parse_inr(value: Any) -> int | None:
    '''"₹310" | "₹1,349.00" | 310 | "FREE" -> 310 | 1349 | 310 | 0'''
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"free", text, re.I):
        return 0
    cleaned = re.sub(r"[₹,\s]", "", text)
    try:
        return round(float(cleaned))
    except ValueError:
        return None


def parse_item_list(value: Any) -> list[ParsedItem]:
    '''"Veg Noodles (1),Malai Chaap (2)" -> [ParsedItem("Veg Noodles",1), ...]

    Splitting on "," is imperfect — a dish legitimately containing a comma would be torn
    in two — but the API offers no delimiter we can trust more, and the "(n)" suffix lets
    us resync on quantities. Items without a "(n)" default to 1 rather than being dropped.
    '''
    if isinstance(value, list):
        # Instamart returns real objects, not a joined string.
        out: list[ParsedItem] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            name = next(
                (
                    entry[k]
                    for k in ("itemName", "name", "displayName")
                    if isinstance(entry.get(k), str) and entry[k].strip()
                ),
                None,
            )
            if not name:
                continue
            qty = entry.get("quantity")
            out.append(
                ParsedItem(name.strip(), qty if isinstance(qty, int) and qty > 0 else 1)
            )
        return out

    if not isinstance(value, str) or not value.strip():
        return []

    items: list[ParsedItem] = []
    for chunk in (c.strip() for c in value.split(",")):
        if not chunk:
            continue
        match = re.fullmatch(r"(.*?)\s*\((\d+)\)", chunk)
        if match:
            items.append(ParsedItem(match.group(1).strip(), int(match.group(2)) or 1))
        else:
            items.append(ParsedItem(chunk))
    return [i for i in items if i.name]


_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ]
    )
    for m in names
}

# Absorbs timezone skew between us and Swiggy, rather than throwing an order back a
# whole year because it looks a few hours in the future.
_TOLERANCE = dt.timedelta(days=2)


def parse_ordered_on(value: Any, now: dt.datetime | None = None) -> dt.date | None:
    '''"August 22, 10:13 PM" -> date(2026, 8, 22), inferring the missing year.

    The API omits the year entirely. The only defensible inference is "the most recent
    occurrence of this month/day that is not in the future".
    '''
    if not isinstance(value, str):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)

    # ISO timestamps (Instamart's `createdAt`) need none of this guesswork.
    if re.search(r"\d{4}", value):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    match = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2})", value)
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    day = int(match.group(2))
    if month is None or not 1 <= day <= 31:
        return None

    year = now.year
    try:
        candidate = dt.date(year, month + 1, day)
    except ValueError:
        return None  # e.g. "February 31" — reject rather than roll over
    if candidate > (now + _TOLERANCE).date():
        try:
            candidate = dt.date(year - 1, month + 1, day)
        except ValueError:
            return None
    return candidate


def parse_food_order(raw: dict[str, Any], now: dt.datetime | None = None) -> ParsedOrder | None:
    external = raw.get("orderId")
    if not isinstance(external, (str, int)):
        return None
    return ParsedOrder(
        source="food",
        external_id=str(external),
        ordered_on=parse_ordered_on(raw.get("orderedTime"), now),
        ordered_time_raw=raw.get("orderedTime") if isinstance(raw.get("orderedTime"), str) else None,
        total_inr=parse_inr(raw.get("orderTotal")),
        status=raw.get("orderStatus") if isinstance(raw.get("orderStatus"), str) else None,
        merchant=raw.get("restaurantName") if isinstance(raw.get("restaurantName"), str) else None,
        items=parse_item_list(raw.get("orderedItems")),
    )


def parse_instamart_order(raw: dict[str, Any], now: dt.datetime | None = None) -> ParsedOrder | None:
    external = raw.get("orderId")
    if not isinstance(external, (str, int)):
        return None
    status = next(
        (raw[k] for k in ("currentStatus", "status") if isinstance(raw.get(k), str)), None
    )
    return ParsedOrder(
        source="instamart",
        external_id=str(external),
        ordered_on=parse_ordered_on(raw.get("createdAt"), now),
        ordered_time_raw=raw.get("createdAt") if isinstance(raw.get("createdAt"), str) else None,
        total_inr=parse_inr(raw.get("totalAmount")),
        status=status,
        merchant=raw.get("storeName") if isinstance(raw.get("storeName"), str) else "Instamart",
        items=parse_item_list(raw.get("items")),
    )


def item_key(name: str) -> str:
    """Cache key for a nutrition estimate — stable across casing and spacing."""
    return re.sub(r"\s+", " ", name.strip().lower())
