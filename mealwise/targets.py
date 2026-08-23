"""Daily nutrition targets derived from the onboarding answers.

Mifflin-St Jeor for resting energy, a light activity factor, then a goal adjustment.
This is a general-population estimate, NOT medical advice — every caller labels it as an
estimate and avoids clinical claims.

Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Targets", "resting_energy", "derive_targets", "validate_target_input", "GOALS", "DIETS"]

GOALS = {
    "protein": "Eat more protein",
    "lose": "Lose weight",
    "gain": "Gain weight",
    "maintain": "Maintain weight",
    "general": "Improve general nutrition",
}

DIETS = ("vegetarian", "non-vegetarian", "eggitarian", "vegan")

# Light activity: the target user is a desk-bound professional working long hours, so
# assuming anything higher would inflate every calorie target.
_ACTIVITY_FACTOR = 1.375

# Protein grams per kg of bodyweight. Higher when losing, to spare lean mass.
_PROTEIN_PER_KG = {"lose": 1.6, "gain": 1.8, "protein": 1.8, "maintain": 1.2, "general": 1.2}

_KCAL_MULTIPLIER = {"lose": 0.85, "gain": 1.1, "protein": 1.0, "maintain": 1.0, "general": 1.0}


@dataclass(frozen=True)
class Targets:
    kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    veg_servings: int = 4
    fruit_servings: int = 2


def resting_energy(*, age_years: int, sex: str, height_cm: float, weight_kg: float) -> float:
    """Mifflin-St Jeor.

    "other" averages the two sex constants rather than guessing one, which is the
    least-wrong option when the equation only offers two.
    """
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age_years
    constant = 5.0 if sex == "male" else -161.0 if sex == "female" else (5 - 161) / 2
    return base + constant


def derive_targets(
    *, age_years: int, sex: str, height_cm: float, weight_kg: float, goal: str
) -> Targets:
    maintenance = resting_energy(
        age_years=age_years, sex=sex, height_cm=height_cm, weight_kg=weight_kg
    ) * _ACTIVITY_FACTOR
    kcal = round(maintenance * _KCAL_MULTIPLIER[goal])

    protein_g = round(weight_kg * _PROTEIN_PER_KG[goal])
    # Fat at ~28% of energy, a middle-of-the-range figure; 9 kcal per gram.
    fat_g = round(kcal * 0.28 / 9)
    # Carbs take the remaining energy at 4 kcal/g, floored so an unusual input cannot
    # produce a negative target.
    carbs_g = max(0, round((kcal - protein_g * 4 - fat_g * 9) / 4))

    return Targets(kcal=kcal, protein_g=protein_g, carbs_g=carbs_g, fat_g=fat_g)


def validate_target_input(
    *, age_years: object, sex: object, height_cm: object, weight_kg: object, goal: object
) -> str | None:
    """Guard the numbers before the maths, so a typo cannot yield a wild target."""
    try:
        age, height, weight = int(age_years), float(height_cm), float(weight_kg)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "Age, height and weight must be numbers."
    if not 13 <= age <= 100:
        return "Age must be between 13 and 100."
    if not 120 <= height <= 230:
        return "Height must be between 120 and 230 cm."
    if not 30 <= weight <= 250:
        return "Weight must be between 30 and 250 kg."
    if sex not in ("male", "female", "other"):
        return "Pick an option for sex."
    if goal not in GOALS:
        return "Pick a health goal."
    return None
