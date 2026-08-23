"""Fetch real, in-stock Instamart products for the agent to recommend FROM.

The agent never names a product from memory. Field names here are the live ones
(`displayName`, `quantityDescription`, `price.offerPrice`, `isInStockAndAvailable`),
none of which the published docs specify.
"""

from __future__ import annotations

from .ai.recommend import Candidate
from .history import parse_inr
from .store import swiggy_session
from .swiggy import InstamartOrdering

__all__ = ["fetch_candidates", "queries_for"]


def queries_for(*, low_protein: bool, low_veg: bool, low_fruit: bool) -> list[str]:
    """Which groceries to search, biased by the gap being closed."""
    queries: list[str] = []
    if low_protein:
        queries += ["paneer", "eggs", "greek yogurt", "dal"]
    if low_veg:
        queries += ["spinach", "mixed vegetables"]
    if low_fruit:
        queries += ["banana", "apple"]
    return queries or ["milk", "banana", "eggs", "paneer"]


async def fetch_candidates(
    token: str, queries: list[str], per_query: int = 4
) -> tuple[str, list[Candidate]]:
    async with swiggy_session(token, "instamart") as session:
        im = InstamartOrdering(session)
        addresses = await im.addresses()
        if not addresses:
            raise RuntimeError("no saved Swiggy addresses on this account")
        address_id = addresses[0]["id"]

        seen: set[str] = set()
        candidates: list[Candidate] = []
        for query in queries:
            found = await im.search_products(address_id, query)
            taken = 0
            for product in found.get("products") or []:
                for variation in product.get("variations") or []:
                    if taken >= per_query:
                        break
                    spin_id = variation.get("spinId")
                    if not spin_id or spin_id in seen:
                        continue
                    if variation.get("isInStockAndAvailable") is False:
                        continue
                    seen.add(spin_id)
                    taken += 1
                    price = variation.get("price") or {}
                    max_qty = variation.get("maxQuantity")
                    candidates.append(
                        Candidate(
                            spin_id=spin_id,
                            name=product.get("displayName") or product.get("brand") or "(unnamed)",
                            quantity_description=variation.get("quantityDescription"),
                            price_inr=parse_inr(price.get("offerPrice") or price.get("mrp")),
                            in_stock=True,
                            max_quantity=max_qty if isinstance(max_qty, int) else None,
                        )
                    )
        return address_id, candidates
