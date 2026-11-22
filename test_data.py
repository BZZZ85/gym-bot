import unittest
from datetime import datetime, timezone

from webapp.data import normalize_records, sessions


def row(reps="10", weight="50", exercise="Жим", stamp=None):
    return {"exercise": exercise, "reps": reps, "weight": weight, "date": stamp or datetime(2026, 11, 21, 22, 30)}


class LegacyDataTests(unittest.TestCase):
    def test_utc_timestamp_is_grouped_by_moscow_day(self):
        records = normalize_records([row()])
        self.assertEqual(records[0]["day"], "2026-11-22")
        self.assertTrue(records[0]["date"].endswith("+03:00"))

    def test_aware_timestamp_same_as_naive_utc(self):
        self.assertEqual(normalize_records([row()]), normalize_records([row(stamp=datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc))]))

    def test_placeholders_do_not_count_as_workouts(self):
        self.assertEqual(sessions(normalize_records([row("0", "0"), row("", "") ])), [])

    def test_bodyweight_sets_count_and_have_zero_volume(self):
        workouts = sessions(normalize_records([row("12", "0", "Подтягивания")]))
        self.assertEqual(workouts[0]["sets"], 1)
        self.assertEqual(workouts[0]["volume"], 0)

    def test_legacy_lists_keep_set_positions(self):
        records = normalize_records([row("10 0 8", "50 60 70")])
        self.assertEqual([(r["reps"], r["weight"]) for r in records], [(10, 50), (8, 70)])
        self.assertEqual(sessions(records)[0]["volume"], 1060)

    def test_single_weight_reused_for_legacy_repetitions(self):
        records = normalize_records([row("10 8 6", "52,5")])
        self.assertEqual([r["weight"] for r in records], [52.5] * 3)

    def test_invalid_numbers_do_not_poison_summary(self):
        records = normalize_records([row("NaN inf -1 10", "NaN")])
        self.assertEqual(len(records), 1)
        self.assertEqual(sessions(records)[0]["volume"], 0)

    def test_exercises_in_same_day_are_one_workout(self):
        workouts = sessions(normalize_records([row(), row(exercise="Присед")]))
        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0]["sets"], 2)
        self.assertEqual(workouts[0]["exercises"], ["Жим", "Присед"])


if __name__ == "__main__":
    unittest.main()
