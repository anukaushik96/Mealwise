"""The recommendation engine.

Two deliberate constraints:
  - at most THREE options, each with a stated reason;
  - optimise health x preference x budget, not health alone — the prompt is told
    explicitly that the cheaper, slightly-less-optimal option can be the right pick.

This recommends from a CANDIDATE LIST already fetched from Instamart, never from the
model's memory of what Swiggy sells. An invented product cannot be ordered.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field

from ..budget import BudgetState

__all__ = ["Candidate", "Option", "Recommendation", "recommend", "MODEL"]

MODEL = "claude-opus-5"


@dataclass(frozen=True)
class Candidate:
    spin_id: str
    name: str
    quantity_description: str | None
    price_inr: int | None
    in_stock: bool
    max_quantity: int | None


class Option(BaseModel):
    spin_id: str = Field(description="spinId copied EXACTLY from the candidate list")
    label: str = Field(description="Product name as shown to the user")
    quantity: int = Field(description="Units to order", ge=1)
    price_inr: int = Field(description="Unit price in rupees, from the candidate list")
    reason: str = Field(
        description="One sentence: why this, referencing the nutrition gap AND the budget"
    )
    adds_protein_g: float = Field(description="Estimated protein this adds")
    adds_veg_servings: float = Field(description="Estimated vegetable servings this adds")


class Recommendation(BaseModel):
    headline: str = Field(
        description="One plain sentence on where they stand, e.g. \"You're low on protein today.\""
    )
    options: list[Option] = Field(description="At most three, best first", max_length=3)
    skipped_reason: str | None = Field(
        description="If nothing suitable exists in the candidates, say why and leave options empty"
    )


SYSTEM = """You are Mealwise, a food and spending agent for Indian users ordering groceries on Swiggy Instamart.

You are given: the user's nutrition targets and what they have consumed today, their
budget state, their dietary constraints, and a list of REAL products currently available
at their address with real prices.

Rules:
- Recommend AT MOST THREE options, best first. Fewer is better than padding.
- Every option must state a reason that references BOTH the nutrition gap and the money.
- Optimise health x preference x budget x convenience — NOT health alone. A cheaper
  option that captures most of the nutritional benefit is often the better
  recommendation; say so when it is.
- Use ONLY products from the candidate list, and copy spin_id EXACTLY. Never invent a
  product, a price or an id.
- Respect dietary constraints absolutely. Never recommend something the user is allergic
  to or that breaks their diet, whatever the nutrition maths says.
- Skip out-of-stock candidates, and never exceed a product's max quantity.
- Instamart has a Rs 99 minimum and charges a small-cart fee below roughly Rs 99; below
  about Rs 199 it also charges delivery. Prefer baskets that clear those thresholds when
  it genuinely saves money, and say so.
- Orders of Rs 1000 or more are refused by the platform. Never propose one.
- If nothing in the candidate list suits, return no options and explain why in
  skipped_reason. Do not force a recommendation.
- Speak plainly. The user should never see a calculation, just the conclusion."""


def recommend(
    *,
    nutrition: dict[str, float],
    targets: dict[str, int],
    budget: BudgetState,
    diet: str | None,
    allergies: list[str],
    dislikes: list[str],
    candidates: list[Candidate],
) -> Recommendation:
    available = [c for c in candidates if c.in_stock and c.price_inr is not None]
    if not available:
        return Recommendation(
            headline="Nothing is available to recommend at this address right now.",
            options=[],
            skipped_reason="No in-stock candidates with a readable price were returned.",
        )

    lines = [
        "TODAY'S NUTRITION (estimated from order history)",
        f"  protein    {nutrition.get('protein_g', 0)}g of {targets.get('protein_g', '?')}g target",
        f"  calories   {nutrition.get('kcal', 0)} of {targets.get('kcal', '?')}",
        f"  vegetables {nutrition.get('veg_servings', 0)} of {targets.get('veg_servings', '?')} servings",
        f"  fruit      {nutrition.get('fruit_servings', 0)} of {targets.get('fruit_servings', '?')} servings",
        "",
        "BUDGET",
        f"  Rs {budget.remaining_inr} left of Rs {budget.budget_inr}, {budget.days_remaining} days to go",
        f"  today's recommended spend: Rs {budget.recommended_daily_inr} (status: {budget.status})",
        "",
        "CONSTRAINTS",
        f"  diet: {diet or 'unspecified'}",
        f"  allergies: {', '.join(allergies) if allergies else 'none'}",
        f"  dislikes: {', '.join(dislikes) if dislikes else 'none'}",
        "",
        "AVAILABLE PRODUCTS (spinId | name | size | price | max qty)",
    ]
    lines += [
        f"  {c.spin_id} | {c.name} | {c.quantity_description or '-'} | Rs {c.price_inr} | max {c.max_quantity or '-'}"
        for c in available
    ]

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_format=Recommendation,
        messages=[{"role": "user", "content": "\n".join(lines)}],
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError("recommendation returned no parseable output")

    # Defence in depth: the prompt forbids inventing ids, but a hallucinated spinId would
    # fail at update_cart with a confusing error, so drop unknown ids here instead.
    known = {c.spin_id for c in available}
    parsed.options = [o for o in parsed.options if o.spin_id in known]
    return parsed
