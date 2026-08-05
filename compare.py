"""
이미지 중복 탐색기 - 핵심 비교 엔진 (메인 진입점)

파일 분할 구조:
- state.py      : 공유 전역 상태 (중단 이벤트, 메모리 캐시, 락)
- database.py   : SQLite 연결, 스키마, 비동기 쓰기 스레드
- hasher.py     : 해시 계산 및 캐시 관리
- comparator.py : 비교 캐시, 버킷/후보 선별, 비교 실행, 중복 그룹 관리
- collector.py  : 파일 수집, 옵션 해석, 증분 대상 선별
- results.py    : 중복 결과 저장/로드 (JSON/텍스트/DB)

이 파일은 메인 진입점(try_compare)과 외부 호환용 re-export를 담당한다.
"""

import os
import time

from logger import logger
from config import load_config

from database import (
    DB_FILE,
    DB_TIMEOUT,
    db_lock,
    init_db,
    _flush_db_writes,
    stop_db_writer,
    set_db_write_options,
)
from state import (
    stop_event,
    hash_memory_cache,
    hash_memory_lock,
    compare_memory_cache,
    compare_memory_lock,
    duplicate_pairs,
    duplicates_lock,
    request_stop,
    reset_stop,
    is_stop_requested,
)
from hasher import (
    get_hash_process_pool,
    compute_hash_worker,
    get_file_hash,
    get_cached_file_hash,
    precompute_hashes,
    set_hash_worker_count,
)
from comparator import (
    make_pair_key,
    already_compared,
    add_compare_record,
    get_processed_compare_files,
    update_processed_compare_files,
    record_duplicate_pair,
    build_groups,
    get_duplicate_groups,
    save_duplicate_results_to_db,
    load_duplicate_results_from_db,
    remove_missing_files_from_cache,
    hash_to_int,
    get_hash_prefix_bits,
    hash_prefix_key,
    build_hash_buckets,
    collect_candidate_pairs,
    filter_batch_candidates,
    compare_files,
    compare_file_with_list,
    preload_compare_cache,
    set_compare_worker_count,
)
from collector import (
    _resolve_compare_options,
    _collect_files_for_mode,
    _apply_max_compare_files,
    select_incremental_compare_targets,
    select_hash_precompute_targets,
    _prepare_incremental_targets,
    _accumulate_compare_progress,
    _describe_hash_precompute_targets,
    _calculate_log_interval,
    _estimate_compare_total_pairs,
    _run_compare_branch,
)
from results import (
    _search_options_suffix,
    duplicate_results_json_path,
    resolve_search_options,
    format_result_filename,
    write_result_file_if_any,
    save_duplicate_results_json,
    _parse_duplicate_text_file,
    _load_groups_from_json,
    load_duplicate_results_json,
)


# ============================================================
# 메인 비교 진입점
# ============================================================
def _has_remaining_compare_work(stopped_early, compare_file_paths, method, hash_size, max_hash_compute_files):
    """
    남은 비교 작업이 있는지 확인.
    
    해시 계산 실패(None) 파일은 메모리 캐시에 기록되어 "이미 처리됨"으로 간주.
    - 최초 계산 실패 파일이 아니라, 실제로 아직 해시 계산이 시도되지 않은 파일만 남은 작업으로 판정.
    - 무한 재시도 방지: 해시 실패 파일은 DB에도 없고 메모리 캐시(None)에 있으므로 재시도 대상에서 제외.
    """
    if stopped_early:
        return True
    if max_hash_compute_files <= 0 or not compare_file_paths:
        return False

    # 메모리 캐시에 이미 기록된 파일(None 포함)은 "이미 처리됨"으로 간주
    with hash_memory_lock:
        not_attempted = [p for p in compare_file_paths if (p, method, hash_size) not in hash_memory_cache]
    if not not_attempted:
        return False

    from hasher import _query_cached_hashes
    db_hashes = _query_cached_hashes(not_attempted, method, hash_size)
    # DB에도 없고 아직 메모리 캐시에 없는 파일만 남은 작업
    return any(os.path.isfile(p) and p not in db_hashes for p in not_attempted)


def _compare_result(total_duplicates, compare_file_paths, method, hash_size, max_hash_compute_files, stopped_early=False):
    """비교 결과 반환"""
    return {
        "total_duplicates": total_duplicates,
        "has_remaining": _has_remaining_compare_work(
            stopped_early, compare_file_paths, method, hash_size, max_hash_compute_files
        ),
    }


def try_compare(folder_list):
    """비교 실행 진입점"""
    reset_stop()
    start_time = time.perf_counter()
    logger.info("[bold yellow][비교 시작][/bold yellow]")
    options = load_config()
    compare_options = _resolve_compare_options(options)
    # 스레드/프로세스 갯수 설정 (0이면 CPU 코어 기반 자동)
    set_hash_worker_count(compare_options.get("hash_worker_count", 0))
    set_compare_worker_count(compare_options.get("compare_worker_count", 0))
    # DB 비동기 쓰기 옵션 설정
    set_db_write_options(
        flush_interval=options.get("db_flush_interval", 2.0),
        batch_size=options.get("db_batch_size", 1000),
    )
    search_mode = compare_options["search_mode"]
    include_sub = compare_options["include_sub"]
    method = compare_options["method"]
    hash_size = compare_options["hash_size"]
    tolerance_rate = compare_options["tolerance_rate"]
    tolerance = compare_options["tolerance"]
    duplicate_limit = compare_options["duplicate_limit"]
    max_compare_files = compare_options["max_compare_files"]
    max_hash_compute_files = compare_options["max_hash_compute_files"]
    compare_progress_log_interval = compare_options["compare_progress_log_interval"]
    save_duplicate_results = compare_options["save_duplicate_results"]
    use_compare_cache = compare_options["use_compare_cache"]
    aspect_ratio_tol = compare_options["aspect_ratio_tol"]
    logger.info(f"Options: mode={search_mode}, include_sub={include_sub}, method={method}, hash_size={hash_size}, tolerance={tolerance} ({tolerance_rate}), duplicate_limit={duplicate_limit}, max_compare_files={max_compare_files}, max_hash_compute_files={max_hash_compute_files}, progress_log_interval={compare_progress_log_interval}, save_results={save_duplicate_results}, load_saved_results={compare_options['load_saved_results']}, auto_open={compare_options['auto_open_duplicate_results']}, use_compare_cache={use_compare_cache}")

    # 이전 저장된 결과 로드
    if compare_options["load_saved_results"]:
        groups = load_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
        if groups:
            logger.info(f"[bold cyan][알림] 이전 저장된 중복 결과 {len(groups)}개 그룹을 불러왔습니다.")

    # 체크된 폴더만 검색 대상으로 사용
    # folder_list는 컨테이너 (Treeview + 필터 + 선택 버튼) 구조
    try:
        from folder_list import get_checked_folders
        folders = get_checked_folders(folder_list)
    except Exception:
        # 이전 호환 (Listbox 직접 사용)
        folders = [entry.split(": ", 1)[1] for entry in folder_list.get(0, "end")]
    logger.info(f"[bold cyan][알림] 검사 폴더 {len(folders)}개 (체크된 것만)[/bold cyan]")
    init_db()

    # 비교 캐시를 메모리에 선로드 (DB 조회 병목 제거)
    if use_compare_cache:
        try:
            preload_compare_cache(method, hash_size, compare_options.get("max_memory_mb", 0))
        except Exception:
            pass

    try:
        total_compared, total_duplicates, total_pairs, compare_file_paths = _run_compare_branch(
            search_mode,
            folders,
            include_sub,
            options,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            max_compare_files,
            max_hash_compute_files,
            use_compare_cache,
            start_time,
            aspect_ratio_tol,
            tolerance_rate,
            compare_progress_log_interval,
        )

        elapsed_time = time.perf_counter() - start_time
        if is_stop_requested():
            logger.warning(f"[bold yellow][비교 중단됨][/bold yellow] 사용자에 의해 중단되었습니다. (진행: {total_compared:,} / {total_pairs:,}회, 소요 시간: {elapsed_time:.2f}초)")
        result_path = None
        json_path = None
        try:
            # txt 결과 저장 여부 설정 (config의 save_txt_results, 기본 True)
            save_txt_results = options.get("save_txt_results", True)
            if save_txt_results:
                result_path = write_result_file_if_any(method, hash_size, aspect_ratio_tol, tolerance_rate)
                if result_path:
                    logger.info(f"[bold green][결과 저장][/bold green] {result_path}")
        except Exception:
            pass

        try:
            json_path = save_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
            if json_path and save_duplicate_results:
                logger.info(f"[bold green][JSON 저장][/bold green] {json_path}")
        except Exception:
            pass

        # 중복 결과를 DB에도 저장
        try:
            save_duplicate_results_to_db(method, hash_size)
        except Exception:
            pass

        if json_path:
            logger.info(f"[bold green][비교 완료][/bold green] 소요 시간: {elapsed_time:.2f}초 (총 {total_compared:,}회 비교, JSON 저장: {json_path})")
        else:
            logger.info(f"[bold green][비교 완료][/bold green] 소요 시간: {elapsed_time:.2f}초 (총 {total_compared:,}회 비교)")
        return _compare_result(
            total_duplicates,
            compare_file_paths,
            method,
            hash_size,
            max_hash_compute_files,
            stopped_early=is_stop_requested(),
        )

    except Exception as e:
        elapsed_time = time.perf_counter() - start_time
        logger.error(f"[bold red][에러 발생][/bold red] (소요 시간: {elapsed_time:.2f}초) {e}", exc_info=True)
        raise
    finally:
        _flush_db_writes()


# ============================================================
# 외부 호환용 re-export
# (기존 모듈들이 compare.py에서 import하던 모든 이름)
# ============================================================
__all__ = [
    # database
    "DB_FILE", "DB_TIMEOUT", "db_lock", "init_db", "stop_db_writer",
    # state
    "stop_event", "hash_memory_cache", "hash_memory_lock",
    "compare_memory_cache", "compare_memory_lock",
    "duplicate_pairs", "duplicates_lock",
    "request_stop", "reset_stop", "is_stop_requested",
    # hasher
    "get_hash_process_pool", "compute_hash_worker",
    "get_file_hash", "get_cached_file_hash", "precompute_hashes",
    # comparator
    "make_pair_key", "already_compared", "add_compare_record",
    "get_processed_compare_files", "update_processed_compare_files",
    "record_duplicate_pair", "build_groups", "get_duplicate_groups",
    "save_duplicate_results_to_db", "load_duplicate_results_from_db",
    "remove_missing_files_from_cache",
    "hash_to_int", "get_hash_prefix_bits", "hash_prefix_key",
    "build_hash_buckets", "collect_candidate_pairs", "collect_candidate_pairs_bktree", "filter_batch_candidates",
    "compare_files", "compare_file_with_list", "preload_compare_cache",
    # collector
    "_resolve_compare_options", "_collect_files_for_mode", "_apply_max_compare_files",
    "select_incremental_compare_targets", "select_hash_precompute_targets",
    "_prepare_incremental_targets", "_accumulate_compare_progress",
    "_describe_hash_precompute_targets", "_calculate_log_interval",
    "_estimate_compare_total_pairs", "_run_compare_branch",
    # results
    "_search_options_suffix", "duplicate_results_json_path", "resolve_search_options",
    "format_result_filename", "write_result_file_if_any",
    "save_duplicate_results_json", "_parse_duplicate_text_file",
    "_load_groups_from_json", "load_duplicate_results_json",
    # main
    "_has_remaining_compare_work", "_compare_result", "try_compare",
]