"""
파일 수집 및 옵션 해석 모듈.

검색 모드/하위 폴더 옵션에 따른 파일 수집,
설정 파일 옵션 해석, 증분 비교 대상 선별.
"""

import os

from config import load_config
from logger import logger
from comparator import get_processed_compare_files, get_hash_prefix_bits, build_hash_buckets, collect_candidate_pairs, collect_candidate_pairs_bktree
from database import get_last_compare_params, set_last_compare_params, reset_compare_progress
from hasher import precompute_hashes, get_hash_compute_count
from state import is_stop_requested


# ============================================================
# 옵션 해석
# ============================================================
def _resolve_compare_options(options):
    """설정 파일에서 비교 옵션 해석"""
    search_mode = options.get("search_mode", "all_folders")
    include_sub = options.get("include_subfolders", "include") == "include"
    method = options.get("compare_method", "ahash")
    hash_size = int(options.get("hash_size", 8))
    tolerance_rate = float(options.get("tolerance_rate", 0.05))
    # tolerance_hamming(정수 해밍 거리)이 있으면 우선 사용, 없으면 tolerance_rate에서 변환
    tolerance_hamming = options.get("tolerance_hamming", None)
    if tolerance_hamming is not None:
        try:
            tolerance = max(0, min(hash_size * hash_size, int(tolerance_hamming)))
        except Exception:
            tolerance = max(0, min(hash_size * hash_size, int(round(tolerance_rate * hash_size * hash_size))))
    else:
        tolerance = max(0, min(hash_size * hash_size, int(round(tolerance_rate * hash_size * hash_size))))
    duplicate_limit = options.get("duplicate_limit_count", 1000)
    try:
        duplicate_limit = int(duplicate_limit)
        if duplicate_limit < 0:
            duplicate_limit = 0
    except Exception:
        duplicate_limit = 0
    max_compare_files = options.get("max_compare_files", 0)
    try:
        max_compare_files = int(max_compare_files)
        if max_compare_files < 0:
            max_compare_files = 0
    except Exception:
        max_compare_files = 0
    max_hash_compute_files = options.get("max_hash_compute_files", 0)
    try:
        max_hash_compute_files = int(max_hash_compute_files)
        if max_hash_compute_files < 0:
            max_hash_compute_files = 0
    except Exception:
        max_hash_compute_files = 0
    compare_progress_log_interval = options.get("compare_progress_log_interval", 0)
    try:
        compare_progress_log_interval = int(compare_progress_log_interval)
        if compare_progress_log_interval < 0:
            compare_progress_log_interval = 0
    except Exception:
        compare_progress_log_interval = 0
    save_duplicate_results = bool(options.get("save_duplicate_results", False))
    load_saved_results = bool(options.get("load_saved_results_on_start", False))
    auto_open_duplicate_results = bool(options.get("auto_open_duplicate_results", False))
    use_compare_cache = bool(options.get("use_compare_cache", True))
    use_tolerance = bool(options.get("use_tolerance", True))
    if not use_tolerance:
        # 체크 해제 시 완전일치 (해밍 거리 0)
        tolerance = 0
    use_aspect_ratio = bool(options.get("use_aspect_ratio", True))
    aspect_ratio_tol = float(options.get("aspect_ratio_tolerance", 0.02))
    if not use_aspect_ratio:
        # 체크 해제 시 모든 비율 허용
        aspect_ratio_tol = 1.0
    batch_size = int(options.get("hash_precompute_batch_size", 1000))
    max_memory_mb = options.get("max_memory_mb", 0)
    try:
        max_memory_mb = int(max_memory_mb)
        if max_memory_mb < 0:
            max_memory_mb = 0
    except Exception:
        max_memory_mb = 0
    # 스레드/프로세스 갯수 (0이면 CPU 코어 기반 자동)
    hash_worker_count = options.get("hash_worker_count", 0)
    try:
        hash_worker_count = int(hash_worker_count)
        if hash_worker_count < 0:
            hash_worker_count = 0
    except Exception:
        hash_worker_count = 0
    compare_worker_count = options.get("compare_worker_count", 0)
    try:
        compare_worker_count = int(compare_worker_count)
        if compare_worker_count < 0:
            compare_worker_count = 0
    except Exception:
        compare_worker_count = 0
    scan_batch_size = options.get("scan_batch_size", 1000)
    try:
        scan_batch_size = int(scan_batch_size)
        if scan_batch_size < 1:
            scan_batch_size = 1000
    except Exception:
        scan_batch_size = 1000
    return {
        "search_mode": search_mode,
        "include_sub": include_sub,
        "method": method,
        "hash_size": hash_size,
        "tolerance_rate": tolerance_rate,
        "tolerance": tolerance,
        "duplicate_limit": duplicate_limit,
        "max_compare_files": max_compare_files,
        "max_hash_compute_files": max_hash_compute_files,
        "compare_progress_log_interval": compare_progress_log_interval,
        "save_duplicate_results": save_duplicate_results,
        "load_saved_results": load_saved_results,
        "auto_open_duplicate_results": auto_open_duplicate_results,
        "use_compare_cache": use_compare_cache,
        "aspect_ratio_tol": aspect_ratio_tol,
        "batch_size": batch_size,
        "max_memory_mb": max_memory_mb,
        "hash_worker_count": hash_worker_count,
        "compare_worker_count": compare_worker_count,
        "scan_batch_size": scan_batch_size,
    }


# ============================================================
# 파일 수집
# ============================================================
def _collect_files_for_mode(folders, include_sub, search_mode):
    """검색 모드에 따라 파일 수집"""
    if search_mode == "cross_folder":
        folder_files = {}
        all_target_files = []
        for folder in folders:
            files = []
            if include_sub:
                for root_dir, dirs, fs in os.walk(folder):
                    for file in fs:
                        full = os.path.join(root_dir, file)
                        if os.path.isfile(full):
                            files.append(full)
                            all_target_files.append(full)
            else:
                for file in os.listdir(folder):
                    full = os.path.join(folder, file)
                    if os.path.isfile(full):
                        files.append(full)
                        all_target_files.append(full)
            folder_files[folder] = files
        return folder_files, all_target_files

    all_files = []
    for folder in folders:
        if include_sub:
            for root_dir, dirs, fs in os.walk(folder):
                for file in fs:
                    full = os.path.join(root_dir, file)
                    if os.path.isfile(full):
                        all_files.append(full)
        else:
            for file in os.listdir(folder):
                full = os.path.join(folder, file)
                if os.path.isfile(full):
                    all_files.append(full)
    return None, all_files


def _apply_max_compare_files(search_mode, folder_files, all_files, max_compare_files, method, hash_size):
    """
    max_compare_files 적용.
    0 초과 시 기존 비교 파일쌍 제외하고 추가로 비교할 파일 갯수.
    - compare_progress에 이미 처리된 파일은 제외하고 신규 파일만 선택
    """
    if max_compare_files <= 0:
        return folder_files, all_files

    # 이미 처리된 파일 목록 조회 (증분 비교)
    processed_set = set(get_processed_compare_files(method, hash_size))

    if search_mode == "cross_folder":
        selected = []
        new_folder_files = {}
        for folder, files in folder_files.items():
            if len(selected) >= max_compare_files:
                new_folder_files[folder] = []
                continue
            # 이미 처리된 파일 제외하고 신규 파일만 선택
            pending_files = [f for f in files if f not in processed_set]
            take = max_compare_files - len(selected)
            selected_files = pending_files[:take]
            selected.extend(selected_files)
            new_folder_files[folder] = selected_files
        return new_folder_files, selected

    # all_folders 모드: 이미 처리된 파일 제외하고 신규 파일만 선택
    pending_files = [f for f in all_files if f not in processed_set]
    return None, pending_files[:max_compare_files]


# ============================================================
# 증분 비교 대상 선별
# ============================================================
def select_incremental_compare_targets(current_paths, previous_paths):
    """
    증분 비교 대상 선별.
    - current_paths: 현재 파일 목록
    - previous_paths: 이전에 처리된 파일 목록
    - 반환: (신규 파일, 기준 파일)
    """
    current = [p for p in dict.fromkeys(current_paths) if p]
    if not previous_paths:
        return current, []
    previous_set = {p for p in previous_paths if p}
    baseline = [p for p in current if p in previous_set]
    new_files = [p for p in current if p not in previous_set]
    return new_files, baseline


def select_hash_precompute_targets(current_paths, previous_paths):
    """해시 계산 대상 선별 (신규 파일만)"""
    new_files, _ = select_incremental_compare_targets(current_paths, previous_paths)
    return new_files


def _prepare_incremental_targets(compare_file_paths, method, hash_size, tolerance=None, aspect_ratio_tol=None):
    """증분 비교 대상 준비.

    비교 옵션(tolerance, aspect_ratio_tol)이 이전 실행과 달라지면
    처리 완료 목록(compare_progress)을 리셋해 전체 재비교를 유도한다.
    - 해밍 거리 캐시를 저장하므로 재비교는 재해시 없이 빠르게 수행된다.
    """
    params = {"tolerance": tolerance, "aspect_ratio_tol": aspect_ratio_tol}
    if get_last_compare_params(method, hash_size) != params:
        logger.info(
            "[bold cyan][알림] 허용 오차 등 비교 옵션이 변경되어 전체 재비교를 수행합니다.[/bold cyan]"
        )
        reset_compare_progress(method, hash_size)
        set_last_compare_params(method, hash_size, params)

    processed_compare_files = get_processed_compare_files(method, hash_size)
    new_compare_files, baseline_compare_files = select_incremental_compare_targets(compare_file_paths, processed_compare_files)
    return set(new_compare_files), baseline_compare_files, new_compare_files


# ============================================================
# 진행 상황 로깅
# ============================================================
def _accumulate_compare_progress(total_compared, completed_result):
    """비교 진행 상황 누적"""
    completed_pairs, is_duplicate = completed_result
    return total_compared + completed_pairs, is_duplicate


def _describe_hash_precompute_targets(total_candidates, remaining_new_targets, max_new_hashes):
    """해시 계산 대상 설명"""
    already_cached_count = max(0, total_candidates - remaining_new_targets)
    if max_new_hashes > 0:
        effective_target_count = min(remaining_new_targets, max_new_hashes)
    else:
        effective_target_count = remaining_new_targets
    return already_cached_count, effective_target_count


def _calculate_log_interval(total_pairs, configured_interval=None):
    """로그 출력 간격 계산"""
    if configured_interval is not None:
        try:
            configured_interval = int(configured_interval)
        except Exception:
            configured_interval = None
        if configured_interval is not None and configured_interval > 0:
            return configured_interval

    if total_pairs <= 0:
        return 1
    if total_pairs >= 50_000_000:
        return 100_000
    if total_pairs >= 5_000_000:
        return 10_000
    if total_pairs >= 500_000:
        return 5_000
    if total_pairs >= 50_000:
        return 1_000
    if total_pairs >= 5_000:
        return 1_000
    return max(1, total_pairs // 10)


def _estimate_compare_total_pairs(search_mode, folder_files, all_files, new_compare_files_set):
    """비교할 총 쌍 수 추정"""
    if search_mode == "cross_folder":
        total_pairs = 0
        keys = list(folder_files.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                f1, f2 = keys[i], keys[j]
                pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
                if pending_files:
                    total_pairs += len(pending_files) * len(folder_files[f2])
        return total_pairs

    pending_files = [p for p in all_files if p in new_compare_files_set]
    return max(0, len(pending_files) * (len(all_files) - 1))


# ============================================================
# 스캔-해시 파이프라인 (배치 단위 병렬 처리)
# ============================================================
def _scan_and_hash_pipeline(folders, include_sub, search_mode, method, hash_size, batch_size, max_new_hashes=0, previous_paths=None):
    """
    파일 스캔과 해시 계산을 배치 단위로 파이프라인 처리.
    - 스캔이 완료되기 전에 해시 계산을 시작하여 전체 시간 단축.
    - 반환: (folder_files, all_files, hashes)
    """
    from hasher import precompute_hashes
    from state import is_stop_requested

    folder_files = {}
    all_files = []
    hashes = {}

    def _process_batch(batch_paths):
        """배치 단위 해시 계산"""
        if not batch_paths or is_stop_requested():
            return {}
        return precompute_hashes(
            batch_paths,
            method,
            hash_size,
            batch_size=batch_size,
            max_new_hashes=max_new_hashes,
            previous_paths=previous_paths,
        )

    if search_mode == "cross_folder":
        for folder in folders:
            files = []
            batch = []
            if include_sub:
                for root_dir, dirs, fs in os.walk(folder):
                    for file in fs:
                        full = os.path.join(root_dir, file)
                        if os.path.isfile(full):
                            files.append(full)
                            all_files.append(full)
                            batch.append(full)
                            if len(batch) >= batch_size:
                                hashes.update(_process_batch(batch))
                                batch = []
                                if is_stop_requested():
                                    break
                    if is_stop_requested():
                        break
            else:
                for file in os.listdir(folder):
                    full = os.path.join(folder, file)
                    if os.path.isfile(full):
                        files.append(full)
                        all_files.append(full)
                        batch.append(full)
                        if len(batch) >= batch_size:
                            hashes.update(_process_batch(batch))
                            batch = []
                            if is_stop_requested():
                                break
            # 남은 배치 처리
            if batch:
                hashes.update(_process_batch(batch))
            folder_files[folder] = files
            if is_stop_requested():
                break
        return folder_files, all_files, hashes

    # all_folders 모드
    batch = []
    for folder in folders:
        if include_sub:
            for root_dir, dirs, fs in os.walk(folder):
                for file in fs:
                    full = os.path.join(root_dir, file)
                    if os.path.isfile(full):
                        all_files.append(full)
                        batch.append(full)
                        if len(batch) >= batch_size:
                            hashes.update(_process_batch(batch))
                            batch = []
                            if is_stop_requested():
                                break
                if is_stop_requested():
                    break
        else:
            for file in os.listdir(folder):
                full = os.path.join(folder, file)
                if os.path.isfile(full):
                    all_files.append(full)
                    batch.append(full)
                    if len(batch) >= batch_size:
                        hashes.update(_process_batch(batch))
                        batch = []
                        if is_stop_requested():
                            break
        if is_stop_requested():
            break
    if batch:
        hashes.update(_process_batch(batch))
    return None, all_files, hashes


# ============================================================
# 해시-비교 동시 파이프라인 (Queue 기반)
# ============================================================
def _run_hash_compare_pipeline(search_mode, folders, include_sub, options, method, hash_size, tolerance, duplicate_limit, max_compare_files, max_hash_compute_files, use_compare_cache, start_time, compare_progress_log_interval=0, aspect_ratio_tol=None):
    """
    해시 계산과 비교를 동시에 실행하는 진정한 파이프라인.
    
    구조:
    [스캔+해시 스레드] → hash_queue → [비교 스레드 풀]
                                         ↓
                                    [중복 그룹 메모리]
    
    - 해시가 계산되는 즉시 비교 스레드로 전달
    - BK-Tree에 증분 추가하며 실시간 비교
    - 중단 시 양쪽 스레드 모두 안전하게 종료
    """
    import queue as queue_mod
    import threading as threading_mod
    from hasher import precompute_hashes, _query_cached_hashes
    from comparator import (
        record_duplicate_pair, compare_file_with_list,
        update_processed_compare_files, _resolve_compare_workers,
        already_compared, add_compare_record, _hash_str_to_int,
        _aspect_ratio_match,
    )
    from bk_match import BKTree, _hash_to_int
    from state import is_stop_requested, request_stop, duplicate_pairs, duplicates_lock, image_sizes, image_sizes_lock
    from logger import logger

    scan_batch = options.get("scan_batch_size", 1000)
    use_bktree = options.get("use_bktree", True)
    # aspect_ratio_tol은 _run_compare_branch에서 이미 해석된 값을 전달받음
    # (options 원본 config에는 aspect_ratio_tol 키가 없고 aspect_ratio_tolerance만 있음)
    
    # 1. 파일 수집 (스캔만 먼저 수행 - 빠름)
    folder_files, all_target_files = _collect_files_for_mode(folders, include_sub, search_mode)
    logger.info(f"total={len(all_target_files)}")
    
    # max_compare_files 적용
    folder_files, all_target_files = _apply_max_compare_files(search_mode, folder_files, all_target_files, max_compare_files, method, hash_size)
    compare_file_paths = list(all_target_files)
    
    # 증분 대상 준비 (비교 옵션 변경 시 전체 재비교 유도)
    new_compare_files_set, baseline_compare_files, new_compare_files = _prepare_incremental_targets(
        compare_file_paths, method, hash_size, tolerance=tolerance, aspect_ratio_tol=aspect_ratio_tol
    )
    if baseline_compare_files:
        logger.info(f"[bold cyan][알림] 증분 비교 모드: 기준 파일 {len(baseline_compare_files)}개, 신규 파일 {len(new_compare_files)}개[/bold cyan]")
    elif new_compare_files:
        logger.info(f"[bold cyan][알림] 신규 파일 {len(new_compare_files)}개를 기준으로 비교를 시작합니다.[/bold cyan]")
    else:
        logger.info("[bold cyan][알림] 새로 추가된 비교 대상이 없어 비교를 건너뜁니다.[/bold cyan]")
        return 0, 0, 0, compare_file_paths

    if is_stop_requested():
        logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
        return 0, 0, 0, compare_file_paths

    # 2. 해시-비교 큐 설정
    # 해시 스레드가 배치를 넣고, 비교 스레드가 가져감
    hash_batch_queue = queue_mod.Queue(maxsize=20)  # 최대 20배치 대기
    all_hashes = {}  # 전체 해시 결과 (스레드 안전하게 접근)
    all_hashes_lock = threading_mod.Lock()
    
    # BK-Tree (비교 스레드에서 증분 추가)
    bktree = BKTree() if use_bktree else None
    bktree_lock = threading_mod.Lock()
    
    # 통계
    stats = {"total_compared": 0, "total_duplicates": 0, "total_hashed": 0}
    stats_lock = threading_mod.Lock()
    
    # 3. 해시 프로듀서 스레드
    def hash_producer():
        """파일을 배치 단위로 해시 계산하여 큐에 전달 (max_hash_compute_files 전역 예산 적용)"""
        try:
            # 전체 파일을 배치로 나누어 해시 계산
            full_file_paths = list(all_target_files)
            chunk_size = max(1, int(scan_batch))

            for start in range(0, len(full_file_paths), chunk_size):
                if is_stop_requested():
                    break

                batch_paths = full_file_paths[start:start + chunk_size]

                # 전역 해시 예산 확인: max_hash_compute_files 초과 시 남은 배치는
                # 자동 재시도가 처리하도록 여기서 중단 (배치별 개별 적용 방지)
                if max_hash_compute_files > 0:
                    already_computed = get_hash_compute_count()
                    remaining_budget = max_hash_compute_files - already_computed
                    if remaining_budget <= 0:
                        logger.info(
                            f"[bold cyan][알림] 새 해시 계산 한도({max_hash_compute_files}개)에 도달하여 "
                            f"남은 {len(full_file_paths) - start}개 파일은 다음 재시도에서 처리합니다.[/bold cyan]"
                        )
                        break
                    # 이번 배치에서 처리할 파일 수를 남은 예산으로 제한
                    budget_paths = batch_paths[:remaining_budget]
                    # precompute_hashes의 max_new_hashes는 남은 예산 기준으로 전달
                    batch_max_new_hashes = remaining_budget
                else:
                    budget_paths = batch_paths
                    batch_max_new_hashes = 0

                batch_hashes = precompute_hashes(
                    budget_paths,
                    method,
                    hash_size,
                    batch_size=scan_batch,
                    max_new_hashes=batch_max_new_hashes,
                )
                
                # 전체 해시에 추가
                with all_hashes_lock:
                    all_hashes.update(batch_hashes)
                    with stats_lock:
                        stats["total_hashed"] += len(batch_hashes)
                
                # 해시 계산 실패(None) 파일은 재시도 대상에서 제외하기 위해 진행 상태에 기록.
                # 이 파일들은 compare_progress에 기록되어 다음 재시도 시 신규 파일에서 제외됨.
                failed_paths = [p for p in budget_paths if batch_hashes.get(p) is None]
                if failed_paths:
                    update_processed_compare_files(method, hash_size, failed_paths)
                    logger.warning(
                        f"[bold yellow][알림] 해시 계산 실패 {len(failed_paths)}개 파일을 다음 재시도에서 제외합니다."
                        f" (예: 이미지가 아닌 파일)[/bold yellow]"
                    )
                
                # 비교 스레드로 전달 (큐가 가득 차면 1초 단위 재시도 - 중단 반응성 확보)
                batch_data = {
                    "paths": budget_paths,
                    "hashes": batch_hashes,
                    "batch_index": start // chunk_size,
                }
                while not is_stop_requested():
                    try:
                        hash_batch_queue.put(batch_data, timeout=1)
                        break
                    except queue_mod.Full:
                        continue
                
                logger.info(f"[bold cyan][해시 파이프라인][/bold cyan] batch {start // chunk_size + 1} 완료: {len(budget_paths)}개 해시 → 비교 큐 전달")
        except Exception as e:
            logger.error(f"[해시 프로듀서 오류] {e}")
        finally:
            # 종료 신호 (큐가 가득 차면 컨슈머가 소비할 때까지 재시도)
            # 컨슈머는 1초마다 큐를 비우므로 최대 60초 내에 전달 보장
            # (중단 시에는 전달 시도 없이 종료 - 컨슈머는 큐가 비고
            #  hash_producer가 종료되면 empty_count로 자동 종료)
            if not is_stop_requested():
                for _ in range(60):
                    try:
                        hash_batch_queue.put(None, timeout=1)
                        break
                    except queue_mod.Full:
                        continue
    
    # 4a. 비교 워커 (단일 파일을 BK-Tree에 추가하고 기존 파일과 비교)
    # 워커 풀에서 실행되며, 검사 후반부에도 병렬 처리로 프리징 없이 1초 단위 반응 보장
    def _process_file_against_tree(path, h):
        """단일 파일을 BK-Tree에 추가하고 기존 파일과 비교 (워커 스레드에서 실행)"""
        if is_stop_requested() or h is None:
            return

        # 진행 상태 기록
        update_processed_compare_files(method, hash_size, [path])

        # BK-Tree에 추가하고 후보 찾기 (락은 query+insert 동안만 최소 유지)
        if bktree is not None:
            h_int = _hash_to_int(h)
            if h_int is None:
                return
            with bktree_lock:
                # tolerance 이내의 모든 파일 찾기
                matches = bktree.query(h_int, tolerance)
                # 새 파일을 트리에 추가
                bktree.insert(h_int, path)

            # 후보와 비교 (락 밖)
            for matched_path, dist in matches:
                if is_stop_requested():
                    break
                if matched_path == path:
                    continue

                # 비교 캐시 확인
                if use_compare_cache:
                    cached_hamming = already_compared(path, matched_path, method, hash_size)
                    if cached_hamming is not None:
                        with stats_lock:
                            stats["total_compared"] += 1
                            if int(cached_hamming) <= tolerance:
                                stats["total_duplicates"] += 1
                                try:
                                    record_duplicate_pair(path, matched_path)
                                except Exception:
                                    pass
                        continue

                # 해상도 비율 필터링
                if aspect_ratio_tol is not None and not _aspect_ratio_match(path, matched_path, aspect_ratio_tol):
                    continue

                # 실제 해밍 거리 계산
                h2 = all_hashes.get(matched_path)
                if h2 is None:
                    continue

                h1_int = _hash_str_to_int(h)
                h2_int = _hash_str_to_int(h2)
                if h1_int is None or h2_int is None:
                    continue

                diff = h1_int ^ h2_int
                hamming_distance = diff.bit_count()
                is_dup = hamming_distance <= tolerance

                with stats_lock:
                    stats["total_compared"] += 1
                    if is_dup:
                        stats["total_duplicates"] += 1
                        try:
                            record_duplicate_pair(path, matched_path)
                        except Exception:
                            pass
                        # 중복 제한 확인
                        if duplicate_limit > 0 and stats["total_duplicates"] >= duplicate_limit:
                            logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {stats['total_duplicates']:,}건 도달로 비교를 중단합니다.")
                            request_stop()
                            return

                # 비교 결과 저장 (해밍 거리 저장)
                if use_compare_cache:
                    add_compare_record(path, matched_path, method, hash_size, hamming_distance)
        else:
            # BK-Tree 미사용: 기존 방식 (모든 파일과 비교)
            with all_hashes_lock:
                existing_paths = [p for p in all_hashes.keys() if p != path and all_hashes[p] is not None]

            if existing_paths:
                total, dups = compare_file_with_list(
                    path, existing_paths, method, hash_size, tolerance,
                    use_compare_cache=use_compare_cache,
                    duplicate_limit=duplicate_limit,
                    hashes=all_hashes,
                    aspect_ratio_tol=aspect_ratio_tol,
                )
                with stats_lock:
                    stats["total_compared"] += total
                    stats["total_duplicates"] += dups


    # 4b. 비교 컨슈머 스레드 (워커 풀로 병렬 처리)
    # - 검사 후반부에도 여러 워커가 동시에 비교하여 속도 저하 방지
    # - 1초 타임아웃 + 1초 단위 진행 로그로 UI 반응성/중단 반응성 확보
    def compare_consumer():
        """해시 배치를 가져와 워커 풀에 분배하여 실시간 비교"""
        import queue as queue_mod
        import time as time_mod
        from concurrent.futures import ThreadPoolExecutor as TPE, wait as wait_futures, FIRST_COMPLETED

        max_workers = _resolve_compare_workers()
        last_log_time = time_mod.perf_counter()

        def _safe_stats():
            with stats_lock:
                return stats["total_compared"], stats["total_duplicates"], stats["total_hashed"]

        def _log_progress(force=False):
            nonlocal last_log_time
            now = time_mod.perf_counter()
            if not force and now - last_log_time < 1.0:
                return
            c, d, h = _safe_stats()
            if c == 0 and not force:
                return
            elapsed = now - start_time
            logger.info(
                f"[bold cyan][진행 상황][/bold cyan] "
                f"비교 {c:,}회 | 중복 {d:,}건 | 해시 {h:,}개 | 경과 {elapsed:.1f}초"
            )
            last_log_time = now

        try:
            with TPE(max_workers=max_workers, thread_name_prefix="cmp") as executor:
                pending_futures = set()
                empty_count = 0
                while True:
                    if is_stop_requested():
                        break

                    # 배치 수신 (1초 타임아웃 - 중단 반응성 확보)
                    try:
                        batch_data = hash_batch_queue.get(timeout=1)
                    except queue_mod.Empty:
                        empty_count += 1
                        # 해시 스레드가 종료되었고 큐가 비어있으면 종료
                        if not hash_thread.is_alive() and empty_count >= 3:
                            break
                        _log_progress()
                        continue
                    empty_count = 0

                    if batch_data is None:
                        break  # 해시 스레드 종료 신호

                    batch_paths = batch_data["paths"]
                    batch_hashes = batch_data["hashes"]

                    # 워커에 파일 단위 분배
                    for path in batch_paths:
                        if is_stop_requested():
                            break
                        h = batch_hashes.get(path)
                        if h is None:
                            continue
                        pending_futures.add(
                            executor.submit(_process_file_against_tree, path, h)
                        )

                    # 완료된 워커 수거 (1초 단위)
                    done, _ = wait_futures(pending_futures, timeout=1, return_when=FIRST_COMPLETED)
                    for fut in done:
                        pending_futures.discard(fut)
                        try:
                            fut.result()
                        except Exception:
                            pass

                    _log_progress()
            # 남은 워커 완료 대기
            for fut in pending_futures:
                try:
                    fut.result(timeout=5)
                except Exception:
                    pass
            _log_progress(force=True)
        except Exception as e:
            logger.error(f"[비교 컨슈머 오류] {e}")
    
    # 5. 파이프라인 실행
    logger.info(f"[bold cyan][파이프라인 시작][/bold cyan] 해시 스레드 + 비교 스레드 동시 실행")
    
    hash_thread = threading_mod.Thread(target=hash_producer, daemon=True)
    compare_thread = threading_mod.Thread(target=compare_consumer, daemon=True)
    
    hash_thread.start()
    compare_thread.start()
    
    # 양쪽 스레드 완료 대기
    hash_thread.join()
    compare_thread.join()
    
    # 6. 결과 정리
    total_compared = stats["total_compared"]
    total_duplicates = stats["total_duplicates"]
    total_hashed = stats["total_hashed"]
    
    valid = sum(1 for v in all_hashes.values() if v)
    none_count = sum(1 for v in all_hashes.values() if v is None)
    logger.info(f"[bold green][파이프라인 완료][/bold green] 해시: {total_hashed}개 (유효: {valid}, 무효: {none_count}), 비교: {total_compared:,}회, 중복: {total_duplicates:,}건")
    
    # total_pairs 추정 (로그용)
    n = len(all_target_files)
    total_pairs = max(0, len(new_compare_files) * (n - 1))
    
    return total_compared, total_duplicates, total_pairs, compare_file_paths


# ============================================================
# 비교 실행 분기 (메인 로직)
# ============================================================
def _run_compare_branch(search_mode, folders, include_sub, options, method, hash_size, tolerance, duplicate_limit, max_compare_files, max_hash_compute_files, use_compare_cache, start_time, aspect_ratio_tol, tolerance_rate, compare_progress_log_interval=0):
    """비교 실행 분기 - 해시-비교 동시 파이프라인 사용"""
    logger.info(f"[bold cyan]{search_mode} 모드[/bold cyan]")
    if aspect_ratio_tol is not None and aspect_ratio_tol < 1.0:
        logger.info(f"[bold cyan][알림] 해상도 비율 허용 오차: {aspect_ratio_tol:.4f} (이미지 크기 정보가 있을 때만 적용)[/bold cyan]")
    
    total_compared, total_duplicates, total_pairs, compare_file_paths = _run_hash_compare_pipeline(
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
        compare_progress_log_interval,
        aspect_ratio_tol,
    )

    return total_compared, total_duplicates, total_pairs, compare_file_paths
