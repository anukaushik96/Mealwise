"""Nutrition estimation from an item NAME.

The history API gives no nutrition and no per-item price — just strings like
"Malai Chaap (1)". So this is an estimate, and it must be presented as one; `confidence`
exists so the UI can say so per item rather than in fine print.

Items are estimated in ONE batched request and cached by `item_key` upstream, so a
repeat dish is never re-billed.
"""

from __future__ import annotations

from typing import Literal

import anthropic
from pydantic import BaseModel, Field

__all__ = ["Estimate", "estimate_nutrition", "MODEL"]

MODEL = "claude-opus-5"


class Estimate(BaseModel):
    item: str = Field(description="The item name, echoed back verbatim so rows can be matched")
    kcal: int = Field(description="Estimated calories for one serving as ordered")
    protein_g: float = Field(description="Grams of protein")
    carbs_g: float = Field(description="Grams of carbohydrate")
    fat_g: float = Field(description="Grams of fat")
    veg_servings: float = Field(description="Vegetable servings, 0 if none; a portion of sabzi is ~1")
    fruit_servings: float = Field(description="Fruit servings, 0 if none")
    confidence: Literal["high", "medium", "low"] = Field(
        description="low when the name is ambiguous about size, recipe or preparation"
    )


class _Batch(BaseModel):
    estimates: list[Estimate]


SYSTEM = """You estimate the nutrition of Indian restaurant and grocery food from item names.

You will be given item names taken from Swiggy order history. They are terse and often
omit portion size, preparation and recipe ("Malai Chaap", "Veg Noodles", "Dahi 400g").

Rules:
- Estimate ONE serving as a customer would receive it from an Indian restaurant, or as
  the packaged grocery quantity if the name states one.
- Indian restaurant portions are generous; do not use Western reference portions.
- Restaurant food is cooked with more oil, ghee and cream than home cooking. Reflect that.
- veg_servings counts actual vegetable content, not "it is vegetarian". Paneer is not a
  vegetable. A dal is roughly 0.5. Plain rice, roti and noodles are 0.
- Set confidence to "low" when the name does not pin down size or recipe, "medium" when
  the dish is standard but the portion is unstated, "high" only when the name states a
  quantity or is a packaged product.
- Echo the item name back EXACTLY as given so rows can be matched.
- Return one estimate per input item, in the same order."""


def estimate_nutrition(items: list[str]) -> list[Estimate]:
    unique = list(dict.fromkeys(i.strip() for i in items if i and i.strip()))
    if not unique:
        return []

    client = anthropic.Anthropic()
    listing = "\n".join(f"{i}. {name}" for i, name in enumerate(unique, 1))
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_format=_Batch,
        messages=[
            {
                "role": "user",
                "content": f"Estimate nutrition for these {len(unique)} items:\n\n{listing}",
            }
        ],
    )

    # parsed_output is None when the model could not satisfy the schema — surface that
    # rather than silently recording zeroes as if they were measurements.
    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError("nutrition estimation returned no parseable output")
    return parsed.estimates
