"""Everything that touches both Postgres and Swiggy: tokens, history import, rollups."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from .ai.nutrition import MODEL as NUTRITION_MODEL, estimate_nutrition
from .auth import refresh_access_token
from .crypto import decrypt_token, encrypt_token
from .db import execute, fetch, fetchrow
from .history import item_key, parse_food_order, parse_instamart_order
from .swiggy import FoodOrdering, InstamartOrdering, SwiggySession

__all__ = [
    "access_token_for",
    "swiggy_session",
    "import_history",
    "nutrition_since",
    "spent_since",
    "spend_by_source",
    "DayNutrition",
]

# Refresh on a margin, not on expiry, so a request cannot fail mid-order because the
# token died between building the cart and checking out.
_REFRESH_MARGIN = dt.timedelta(hours=12)


async def access_token_for(user_id: str) -> str:
    row = await fetchrow(
        "SELECT access_token_enc, refresh_token_enc, expires_at FROM swiggy_tokens WHERE user_id = $1",
        user_id,
    )
    if row is None:
        raise RuntimeError("this account has no Swiggy connection — reconnect Swiggy")

    if row["expires_at"] - dt.datetime.now(dt.timezone.utc) > _REFRESH_MARGIN:
        return decrypt_token(row["access_token_enc"])

    if not row["refresh_token_enc"]:
        # Swiggy advertises refresh_token support but may not issue one. Without it the
        # only recovery is fresh consent, so say so plainly rather than failing deeper in.
        raise RuntimeError(
            "Swiggy access token has expired and no refresh token was issued — reconnect Swiggy"
        )

    tokens = await refresh_access_token(decrypt_token(row["refresh_token_enc"]))
    await execute(
        """UPDATE swiggy_tokens
              SET access_token_enc = $2,
                  refresh_token_enc = COALESCE($3, refresh_token_enc),
                  expires_at = $4,
                  updated_at = now()
            WHERE user_id = $1""",
        user_id,
        encrypt_token(tokens.access_token),
        encrypt_token(tokens.refresh_token) if tokens.refresh_token else None,
        tokens.expires_at,
    )
    return tokens.access_token


def swiggy_session(token: str, server: str) -> SwiggySession:
    """Per-request session. A Vercel function may be frozen between invocations, so a
    long-lived MCP transport across that boundary is not safe."""
    return SwiggySession.connect(server, token)  # type: ignore[arg-type]


async def import_history(user_id: str) -> dict[str, int]:
    """Pull order history from both servers into Postgres.

    Notes from the live API that shape this:
      - `get_food_orders` REQUIRES an addressId and returns `{}` (not an error) without
        one — a silent empty that looks exactly like "no orders".
      - Food history is account-wide, not per-address: the same orders come back for
        addresses in different cities, so one addressId is enough.
    """
    token = await access_token_for(user_id)
    parsed = []

    async with swiggy_session(token, "instamart") as session:
        im = InstamartOrdering(session)
        addresses = await im.addresses()
        raw = await im.orders()
        parsed += [
            o for o in (parse_instamart_order(r) for r in (raw or {}).get("orders", [])) if o
        ]

    address_id = addresses[0]["id"] if addresses else None
    if address_id:
        async with swiggy_session(token, "food") as session:
            food = FoodOrdering(session)
            raw = await food.orders(address_id)
            parsed += [
                o for o in (parse_food_order(r) for r in (raw or {}).get("orders", [])) if o
            ]

    imported = skipped = 0
    for order in parsed:
        row = await fetchrow(
            """INSERT INTO orders
                 (user_id, source, external_id, ordered_on, ordered_time_raw,
                  total_inr, status, merchant, raw)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (user_id, source, external_id) DO NOTHING
               RETURNING id""",
            user_id,
            order.source,
            order.external_id,
            order.ordered_on,
            order.ordered_time_raw,
            order.total_inr,
            order.status,
            order.merchant,
            json.dumps({"source": order.source, "status": order.status}),
        )
        if row is None:
            skipped += 1
            continue
        imported += 1
        for item in order.items:
            await execute(
                "INSERT INTO order_items (order_id, name, quantity) VALUES ($1,$2,$3)",
                row["id"],
                item.name,
                item.quantity,
            )

    return {"imported": imported, "skipped": skipped}


@dataclass
class DayNutrition:
    kcal: int = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    veg_servings: float = 0
    fruit_servings: float = 0
    unestimated: list[str] | None = None
    has_low_confidence: bool = False

    def as_dict(self) -> dict[str, float]:
        return {
            "kcal": self.kcal,
            "protein_g": round(self.protein_g),
            "carbs_g": round(self.carbs_g),
            "fat_g": round(self.fat_g),
            "veg_servings": round(self.veg_servings, 1),
            "fruit_servings": round(self.fruit_servings, 1),
        }


async def nutrition_since(user_id: str, since: dt.date) -> DayNutrition:
    """Consumed nutrition since a date, estimated from item names and cached per item."""
    rows = await fetch(
        """SELECT oi.name, oi.quantity
             FROM order_items oi
             JOIN orders o ON o.id = oi.order_id
            WHERE o.user_id = $1 AND o.ordered_on >= $2""",
        user_id,
        since,
    )
    total = DayNutrition(unestimated=[])
    if not rows:
        return total

    cached = {r["item_key"]: r for r in await fetch("SELECT * FROM nutrition_estimates")}
    wanted = {item_key(r["name"]): r["name"] for r in rows}
    missing = [name for key, name in wanted.items() if key not in cached]

    if missing:
        for est in estimate_nutrition(missing):
            key = item_key(est.item)
            await execute(
                """INSERT INTO nutrition_estimates
                     (item_key, kcal, protein_g, carbs_g, fat_g, veg_servings,
                      fruit_servings, confidence, model)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (item_key) DO UPDATE SET
                     kcal = EXCLUDED.kcal, protein_g = EXCLUDED.protein_g,
                     carbs_g = EXCLUDED.carbs_g, fat_g = EXCLUDED.fat_g,
                     veg_servings = EXCLUDED.veg_servings,
                     fruit_servings = EXCLUDED.fruit_servings,
                     confidence = EXCLUDED.confidence, model = EXCLUDED.model""",
                key, est.kcal, est.protein_g, est.carbs_g, est.fat_g,
                est.veg_servings, est.fruit_servings, est.confidence, NUTRITION_MODEL,
            )
            cached[key] = {
                "kcal": est.kcal, "protein_g": est.protein_g, "carbs_g": est.carbs_g,
                "fat_g": est.fat_g, "veg_servings": est.veg_servings,
                "fruit_servings": est.fruit_servings, "confidence": est.confidence,
            }

    for row in rows:
        est = cached.get(item_key(row["name"]))
        if est is None:
            total.unestimated.append(row["name"])  # type: ignore[union-attr]
            continue
        q = row["quantity"] or 1
        total.kcal += int(est["kcal"] or 0) * q
        total.protein_g += float(est["protein_g"] or 0) * q
        total.carbs_g += float(est["carbs_g"] or 0) * q
        total.fat_g += float(est["fat_g"] or 0) * q
        total.veg_servings += float(est["veg_servings"] or 0) * q
        total.fruit_servings += float(est["fruit_servings"] or 0) * q
        if est["confidence"] == "low":
            total.has_low_confidence = True

    return total


async def spent_since(user_id: str, since: dt.date) -> int:
    """Food spend since a date, in whole rupees. Order-level totals only — the history
    API gives no per-item price, so per-dish attribution is not possible."""
    row = await fetchrow(
        "SELECT COALESCE(SUM(total_inr), 0) AS total FROM orders WHERE user_id = $1 AND ordered_on >= $2",
        user_id,
        since,
    )
    return int(row["total"] if row else 0)


async def spend_by_source(user_id: str, since: dt.date) -> dict[str, int]:
    rows = await fetch(
        """SELECT source, COALESCE(SUM(total_inr), 0) AS total
             FROM orders WHERE user_id = $1 AND ordered_on >= $2 GROUP BY source""",
        user_id,
        since,
    )
    return {r["source"]: int(r["total"]) for r in rows}
