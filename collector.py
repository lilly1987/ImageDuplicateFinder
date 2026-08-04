"""
파일 수집 및 옵션 해석 모듈.

검색 모드/하위 폴더 옵션에 따른 파일 수집,
설정 파일 옵션 해석, 증분 비교 대상 선별.
"""

import os

from config import load_config
from logger import logger
from comparator import get_processed_compare_files, get_hash_prefix_bits, build_hash_buckets, collect_candidate_pairs
from hasher import precompute_hashes
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
    aspect_ratio_tol = float(options.get("aspect_ratio_tolerance", 0.02))
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


def _prepare_incremental_targets(compare_file_paths, method, hash_size):
    """증분 비교 대상 준비"""
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
# 비교 실행 분기 (메인 로직)
# ============================================================
def _run_compare_branch(search_mode, folders, include_sub, options, method, hash_size, tolerance, duplicate_limit, max_compare_files, max_hash_compute_files, use_compare_cache, start_time, aspect_ratio_tol, tolerance_rate, compare_progress_log_interval=0):
    """비교 실행 분기"""
    from comparator import _run_cross_folder_compare, _run_all_folder_compare

    total_compared = 0
    total_duplicates = 0
    total_pairs = 0
    compare_file_paths = []

    if search_mode == "cross_folder":
        logger.info("[bold cyan]cross_folder 모드[/bold cyan]")
        # 스캔-해시 파이프라인: 스캔하면서 배치 단위로 해시 계산
        scan_batch = options.get("scan_batch_size", 1000)
        folder_files, all_target_files, hashes = _scan_and_hash_pipeline(
            folders, include_sub, search_mode, method, hash_size,
            batch_size=scan_batch,
            max_new_hashes=max_hash_compute_files,
        )
        logger.info(f"total={len(all_target_files)}")
        folder_files, all_target_files = _apply_max_compare_files(search_mode, folder_files, all_target_files, max_compare_files, method, hash_size)
        compare_file_paths = list(all_target_files)
        new_compare_files_set, baseline_compare_files, new_compare_files = _prepare_incremental_targets(compare_file_paths, method, hash_size)
        if baseline_compare_files:
            logger.info(f"[bold cyan][알림] 증분 비교 모드: 기준 파일 {len(baseline_compare_files)}개, 신규 파일 {len(new_compare_files)}개[/bold cyan]")
        elif new_compare_files:
            logger.info(f"[bold cyan][알림] 신규 파일 {len(new_compare_files)}개를 기준으로 비교를 시작합니다.[/bold cyan]")
        else:
            logger.info("[bold cyan][알림] 새로 추가된 비교 대상이 없어 비교를 건너뜁니다.[/bold cyan]")
            return 0, total_duplicates, 0, compare_file_paths

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        # max_compare_files 적용 후 hashes 필터링
        if max_compare_files > 0:
            target_set = set(all_target_files)
            hashes = {p: h for p, h in hashes.items() if p in target_set}

        valid = sum(1 for v in hashes.values() if v)
        none_count = sum(1 for v in hashes.values() if v is None)
        missing_count = len(all_target_files) - len(hashes)
        logger.info(f"Hashes precomputed: entries={len(hashes)}, valid={valid}, none={none_count}, missing={missing_count}")

        prefix_bits = get_hash_prefix_bits(hash_size)
        bucket_index = build_hash_buckets(all_target_files, hashes, prefix_bits)
        bucket_count = len(bucket_index)
        max_bucket = max((len(v) for v in bucket_index.values()), default=0)
        logger.info(f"Built {bucket_count} buckets (prefix_bits={prefix_bits}), max_bucket_size={max_bucket}")
        candidates = collect_candidate_pairs(all_target_files, bucket_index)

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        estimated_total_pairs = _estimate_compare_total_pairs(search_mode, folder_files, all_target_files, new_compare_files_set)
        log_interval = _calculate_log_interval(estimated_total_pairs, compare_progress_log_interval)
        logger.info(f"Progress log interval: {log_interval} pairs (estimated_total_pairs={estimated_total_pairs:,})")
        total_compared, total_duplicates, total_pairs = _run_cross_folder_compare(
            folder_files,
            new_compare_files_set,
            hashes,
            candidates,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            use_compare_cache,
            start_time,
            log_interval,
            estimated_total_pairs < 50,
        )
    else:
        logger.info("[bold cyan][all_folders 모드][/bold cyan]")
        # 스캔-해시 파이프라인: 스캔하면서 배치 단위로 해시 계산
        scan_batch = options.get("scan_batch_size", 1000)
        _, all_collected_files, hashes = _scan_and_hash_pipeline(
            folders, include_sub, search_mode, method, hash_size,
            batch_size=scan_batch,
            max_new_hashes=max_hash_compute_files,
        )
        _, all_files = _apply_max_compare_files(search_mode, None, all_collected_files, max_compare_files, method, hash_size)
        compare_file_paths = list(all_files)
        new_compare_files_set, baseline_compare_files, new_compare_files = _prepare_incremental_targets(compare_file_paths, method, hash_size)
        if baseline_compare_files:
            logger.info(f"[bold cyan][알림] 증분 비교 모드: 기준 파일 {len(baseline_compare_files)}개, 신규 파일 {len(new_compare_files)}개[/bold cyan]")
        elif new_compare_files:
            logger.info(f"[bold cyan][알림] 신규 파일 {len(new_compare_files)}개를 기준으로 비교를 시작합니다.[/bold cyan]")
        else:
            logger.info("[bold cyan][알림] 새로 추가된 비교 대상이 없어 비교를 건너뜁니다.[/bold cyan]")
            return 0, total_duplicates, 0, compare_file_paths

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        # max_compare_files 적용 후 hashes 필터링
        if max_compare_files > 0:
            target_set = set(all_files)
            hashes = {p: h for p, h in hashes.items() if p in target_set}

        valid = sum(1 for v in hashes.values() if v)
        none_count = sum(1 for v in hashes.values() if v is None)
        missing_count = len(all_files) - len(hashes)
        logger.info(f"Hashes precomputed: entries={len(hashes)}, valid={valid}, none={none_count}, missing={missing_count}")

        prefix_bits = get_hash_prefix_bits(hash_size)
        bucket_index = build_hash_buckets(all_files, hashes, prefix_bits)
        bucket_count = len(bucket_index)
        max_bucket = max((len(v) for v in bucket_index.values()), default=0)
        logger.info(f"Built {bucket_count} buckets (prefix_bits={prefix_bits}), max_bucket_size={max_bucket}")
        candidates = collect_candidate_pairs(all_files, bucket_index)

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        n = len(all_files)
        pending_files = [p for p in all_files if p in new_compare_files_set]
        total_pairs = max(0, len(pending_files) * (n - 1))
        logger.info(f"Starting all_folders compare: total_pairs={total_pairs}, files={n}, pending_files={len(pending_files)}")
        log_interval = _calculate_log_interval(total_pairs, compare_progress_log_interval)
        logger.info(f"Progress log interval: {log_interval} pairs (estimated_total_pairs={total_pairs:,})")
        verbose_single = total_pairs < 50
        total_compared, total_duplicates, total_pairs = _run_all_folder_compare(
            all_files,
            new_compare_files_set,
            hashes,
            candidates,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            use_compare_cache,
            start_time,
            log_interval,
            verbose_single,
        )

    return total_compared, total_duplicates, total_pairs, compare_file_paths