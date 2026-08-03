import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from compare import select_incremental_compare_targets


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


if __name__ == "__main__":
    unittest.main()
