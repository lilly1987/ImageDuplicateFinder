import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from compare import (
    select_incremental_compare_targets,
    select_hash_precompute_targets,
    _calculate_log_interval,
    _accumulate_compare_progress,
    _describe_hash_precompute_targets,
    _prepare_incremental_targets,
    _check_compare_options_changed,
)
import database
from comparator import get_processed_compare_files
import comparator


class _TmpDB:
    """임시 DB로 전환해 비교 옵션 변경 감지 로직을 검증."""
    def __init__(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._orig = database.DB_FILE

    def __enter__(self):
        database.DB_FILE = self._tmp.name
        comparator.DB_FILE = self._tmp.name
        database.init_db()
        return self

    def __exit__(self, *exc):
        database.DB_FILE = self._orig
        comparator.DB_FILE = self._orig
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass
        return False


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

    def test_uses_configured_log_interval_when_provided(self):
        self.assertEqual(_calculate_log_interval(1000000, configured_interval=200), 200)
        self.assertEqual(_calculate_log_interval(100, configured_interval=50), 50)

    def test_accumulates_progress_by_compared_pairs(self):
        self.assertEqual(_accumulate_compare_progress(10, (3, True)), (13, True))
        self.assertEqual(_accumulate_compare_progress(10, (0, False)), (10, False))

    def test_describes_remaining_hash_targets_with_config_limit(self):
        already_cached_count, effective_target_count = _describe_hash_precompute_targets(10000, 14, 5000)
        self.assertEqual(already_cached_count, 9986)
        self.assertEqual(effective_target_count, 14)


class CompareOptionsChangeTests(unittest.TestCase):
    def test_option_change_resets_progress_and_reincludes_all_files(self):
        """_run_hash_compare_pipeline 의 실제 실행 순서를 재현한 통합 검증.

        - 이전 실행에서 tolerance=0 로 전체 파일이 compare_progress 에 기록됨
        - 이번 실행에서 tolerance=1 로 변경됨
        - 기대: _check_compare_options_changed 가 progress 를 리셋해
          _apply_max_compare_files 가 전체 파일을 다시 대상으로 포함하고,
          _prepare_incremental_targets 도 전체 파일을 신규로 반환
        """
        from collector import _apply_max_compare_files
        with _TmpDB():
            method, hash_size = "dhash", 64
            files = ["/data/a.jpg", "/data/b.jpg", "/data/c.jpg"]

            # 1) 이전 실행: tolerance=0, progress 에 전체 파일 기록
            _check_compare_options_changed(method, hash_size, 0, 1.0)
            for p in files:
                database.schedule_progress_record(method, hash_size, p)
            database._flush_db_writes()
            self.assertEqual(
                set(get_processed_compare_files(method, hash_size)), set(files)
            )

            # 2) 이번 실행: tolerance=1 로 변경 → 리셋 후 progress 비어야 함
            _check_compare_options_changed(method, hash_size, 1, 1.0)
            self.assertEqual(
                get_processed_compare_files(method, hash_size), []
            )

            # 3) _apply_max_compare_files 가 전체 파일을 다시 대상으로 포함
            folder_files = {"/data": files}
            new_folder_files, selected = _apply_max_compare_files(
                "cross_folder", folder_files, files, 10000, method, hash_size)
            self.assertEqual(set(selected), set(files))

            # 4) _prepare_incremental_targets 도 전체 파일을 신규로 반환
            new_set, baseline, new_list = _prepare_incremental_targets(files, method, hash_size)
            self.assertEqual(new_set, set(files))
            self.assertEqual(baseline, [])
            self.assertEqual(set(new_list), set(files))

    def test_same_options_do_not_reset_progress(self):
        """동일 옵션 재실행 시 progress 가 유지되어 증분 스킵이 유지된다."""
        with _TmpDB():
            method, hash_size = "dhash", 64
            files = ["/data/a.jpg", "/data/b.jpg"]

            _check_compare_options_changed(method, hash_size, 5, 1.0)
            for p in files:
                database.schedule_progress_record(method, hash_size, p)
            database._flush_db_writes()

            # 같은 tolerance 로 재실행 → 리셋되지 않음 (progress 그대로)
            _check_compare_options_changed(method, hash_size, 5, 1.0)
            self.assertEqual(
                set(get_processed_compare_files(method, hash_size)), set(files)
            )


if __name__ == "__main__":
    unittest.main()
