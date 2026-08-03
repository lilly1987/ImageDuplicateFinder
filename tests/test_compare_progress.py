import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from compare import select_incremental_compare_targets, select_hash_precompute_targets, _calculate_log_interval


class IncrementalCompareTargetTests(unittest.TestCase):
    def test_returns_only_new_files_against_existing_files(self):
        existing = ["/data/a.jpg", "/data/b.jpg"]
        current = ["/data/a.jpg", "/data/b.jpg", "/data/c.jpg", "/data/d.jpg"]

        new_files, baseline_files = select_incremental_compare_targets(current, existing)

        self.assertEqual(new_files, ["/data/c.jpg", "/data/d.jpg"])
        self.assertEqual(baseline_files, ["/data/a.jpg", "/data/b.jpg"])

    def test_uses_current_files_when_previous_state_is_empty(self):
        existing = []
        current = ["/data/a.jpg", "/data/b.jpg"]

        new_files, baseline_files = select_incremental_compare_targets(current, existing)

        self.assertEqual(new_files, ["/data/a.jpg", "/data/b.jpg"])
        self.assertEqual(baseline_files, [])

    def test_selects_only_new_files_for_hash_precompute_in_incremental_run(self):
        existing = ["/data/a.jpg", "/data/b.jpg"]
        current = ["/data/a.jpg", "/data/b.jpg", "/data/c.jpg", "/data/d.jpg"]

        targets = select_hash_precompute_targets(current, existing)

        self.assertEqual(targets, ["/data/c.jpg", "/data/d.jpg"])

    def test_calculates_log_interval_from_total_pairs(self):
        self.assertEqual(_calculate_log_interval(0), 1)
        self.assertEqual(_calculate_log_interval(100), 10)
        self.assertEqual(_calculate_log_interval(5000), 1000)
        self.assertEqual(_calculate_log_interval(10000), 1000)


if __name__ == "__main__":
    unittest.main()
