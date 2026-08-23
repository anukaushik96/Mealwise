"""Nutrition target derivation. These are estimates, but they must be sane estimates."""

import unittest

from mealwise.targets import derive_targets, resting_energy, validate_target_input

RAHUL = dict(age_years=29, sex="male", height_cm=175, weight_kg=75, goal="protein")


class TestRestingEnergy(unittest.TestCase):
    def test_sane_range(self):
        bmr = resting_energy(age_years=29, sex="male", height_cm=175, weight_kg=75)
        self.assertTrue(1500 < bmr < 1900, bmr)

    def test_other_sits_between_male_and_female(self):
        common = dict(age_years=29, height_cm=175, weight_kg=75)
        male = resting_energy(sex="male", **common)
        female = resting_energy(sex="female", **common)
        other = resting_energy(sex="other", **common)
        self.assertLess(female, other)
        self.assertLess(other, male)


class TestDeriveTargets(unittest.TestCase):
    def test_persona(self):
        t = derive_targets(**RAHUL)
        self.assertTrue(1900 < t.kcal < 2700, t.kcal)
        self.assertEqual(t.protein_g, 135)  # 1.8 g/kg

    def test_macros_reconcile_to_calories(self):
        t = derive_targets(**RAHUL)
        from_macros = t.protein_g * 4 + t.carbs_g * 4 + t.fat_g * 9
        self.assertLessEqual(abs(from_macros - t.kcal), 6, f"{from_macros} vs {t.kcal}")

    def test_losing_lowers_calories_but_keeps_protein_high(self):
        lose = derive_targets(**{**RAHUL, "goal": "lose"})
        self.assertLess(lose.kcal, derive_targets(**RAHUL).kcal)
        self.assertGreaterEqual(lose.protein_g, 120)

    def test_no_negative_carbs_for_small_high_protein_case(self):
        t = derive_targets(age_years=60, sex="female", height_cm=150, weight_kg=45, goal="lose")
        self.assertGreaterEqual(t.carbs_g, 0)


class TestValidation(unittest.TestCase):
    def test_accepts_valid(self):
        self.assertIsNone(validate_target_input(**RAHUL))

    def test_rejects_absurd_weight(self):
        self.assertIsNotNone(validate_target_input(**{**RAHUL, "weight_kg": 900}))

    def test_rejects_bad_goal(self):
        self.assertIsNotNone(validate_target_input(**{**RAHUL, "goal": "bulk"}))

    def test_rejects_non_numeric(self):
        self.assertIsNotNone(validate_target_input(**{**RAHUL, "age_years": "abc"}))


if __name__ == "__main__":
    unittest.main()
