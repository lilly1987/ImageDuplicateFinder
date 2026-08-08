"""모듈 import 검증 테스트"""
import os
import sys

# 프로젝트 루트를 sys.path에 추가 (tests/ 디렉토리에서 실행 시)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("Testing imports...")

try:
    import compare
    print("OK: compare")
except Exception as e:
    print(f"FAIL: compare -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import state
    print("OK: state")
except Exception as e:
    print(f"FAIL: state -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import database
    print("OK: database")
except Exception as e:
    print(f"FAIL: database -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import hasher
    print("OK: hasher")
except Exception as e:
    print(f"FAIL: hasher -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import comparator
    print("OK: comparator")
except Exception as e:
    print(f"FAIL: comparator -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import collector
    print("OK: collector")
except Exception as e:
    print(f"FAIL: collector -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import results
    print("OK: results")
except Exception as e:
    print(f"FAIL: results -> {type(e).__name__}: {e}")
    sys.exit(1)

# 외부 모듈 호환성 확인
print("\nChecking external module compatibility...")

try:
    import ui_results
    print("OK: ui_results imports from compare")
except Exception as e:
    print(f"FAIL: ui_results -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import ui_cache
    print("OK: ui_cache imports from compare")
except Exception as e:
    print(f"FAIL: ui_cache -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import folder_list
    print("OK: folder_list")
except Exception as e:
    print(f"FAIL: folder_list -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import ui_options
    print("OK: ui_options")
except Exception as e:
    print(f"FAIL: ui_options -> {type(e).__name__}: {e}")
    sys.exit(1)

try:
    import shortcuts
    print("OK: shortcuts")
except Exception as e:
    print(f"FAIL: shortcuts -> {type(e).__name__}: {e}")
    sys.exit(1)

# compare.py에서 re-export된 이름들 확인
print("\nChecking compare.py re-exports...")
re_export_names = [
    "DB_FILE", "DB_TIMEOUT", "db_lock", "init_db", "stop_db_writer",
    "stop_event", "hash_memory_cache", "hash_memory_lock",
    "compare_memory_cache", "compare_memory_lock",
    "duplicate_pairs", "duplicates_lock",
    "request_stop", "reset_stop", "is_stop_requested",
    "get_hash_process_pool", "compute_hash_worker",
    "get_file_hash", "get_cached_file_hash", "precompute_hashes",
    "make_pair_key", "already_compared", "add_compare_record",
    "get_processed_compare_files", "update_processed_compare_files",
    "record_duplicate_pair", "build_groups", "get_duplicate_groups",
    "save_duplicate_results_to_db", "load_duplicate_results_from_db",
    "remove_missing_files_from_cache",
    "hash_to_int", "get_hash_prefix_bits", "hash_prefix_key",
    "build_hash_buckets", "collect_candidate_pairs", "filter_batch_candidates",
    "compare_files", "compare_file_with_list", "preload_compare_cache",
    "duplicate_results_json_path", "resolve_search_options",
    "format_result_filename", "write_result_file_if_any",
    "save_duplicate_results_json", "load_duplicate_results_json",
    "try_compare",
]
import compare
missing = [name for name in re_export_names if not hasattr(compare, name)]
if missing:
    print(f"FAIL: compare.py에서 누락된 re-export: {missing}")
    sys.exit(1)
print("OK: all compare.py re-exports present")

print("\nALL IMPORT TESTS PASSED")
