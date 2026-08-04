"""
파일 비교 모듈.

비교 캐시 관리, 해시 버킷/후보 선별, 개별 파일 비교, 중복 그룹 관리,
폴더 간/전체 폴더 비교 실행, 중복 결과 DB 저장/로드.
"""

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED, FIRST_COMPLETED

from logger import logger
from database import (
    DB_FILE, DB_TIMEOUT, db_lock,
    schedule_compare_record, schedule_progress_record,
)
from hasher import get_cached_file_hash
from state import (
    compare_memory_cache, compare_memory_lock,
    duplicate_pairs, duplicates_lock,
    hash_memory_cache, hash_memory_lock,
    is_stop_requested, request_stop,
)

# 해시 문자열을 정수로 변환하는 전역 캐시
_hash_int_cache = {}
_hash_int_lock = __import__("threading").Lock()

# 비교 캐시 메모리 선로드 여부
_compare_cache_loaded = False
_compare_cache_loaded_lock = __import__("threading").Lock()

# 비교 스레드 갯수 (0이면 CPU 코어 기반 자동)
_compare_worker_count = 0


def set_compare_worker_count(count):
    """비교 스레드 갯수 설정 (0이면 CPU 코어 기반 자동)"""
    global _compare_worker_count
    _compare_worker_count = max(0, int(count))


def _resolve_compare_workers():
    """비교 스레드 갯수 결정"""
    if _compare_worker_count > 0:
        return min(32, _compare_worker_count)
    return min(32, max(1, (os.cpu_count() or 4) + 4))


# ============================================================
# 비교 캐시 관리
# ============================================================
def make_pair_key(file1, file2):
    """파일 쌍을 정렬된 키로 변환 (1=2, 2=1 중복 방지)"""
    return tuple(sorted([file1, file2]))


def already_compared(file1, file2, method, hash_size):
    """
    이미 비교된 파일 쌍인지 확인.
    - _compare_cache_loaded=True (선로드 완료): 메모리에서만 확인, miss면 None 반환 (DB 조회 없음)
    - _compare_cache_loaded=False: DB에서 조회
    """
    f1, f2 = make_pair_key(file1, file2)
    cache_key = (f1, f2, method, hash_size)

    # 메모리 캐시 확인
    with compare_memory_lock:
        if cache_key in compare_memory_cache:
            return compare_memory_cache[cache_key]

    # 선로드 완료 시: DB 조회 생략 (비교 결과는 add_compare_record로 메모리+비동기 DB 저장)
    if _compare_cache_loaded:
        return None

    # DB 확인
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT is_duplicate FROM compare_cache WHERE file1=? AND file2=? AND method=? AND hash_size=?",
                (f1, f2, method, hash_size)
            )
            row = cur.fetchone()
            result = bool(row[0]) if row else None
        finally:
            conn.close()

    # 메모리 캐시에 저장
    with compare_memory_lock:
        compare_memory_cache[cache_key] = result

    return result


def add_compare_record(file1, file2, method, hash_size, is_duplicate):
    """비교 결과 저장 (메모리 캐시 + DB 비동기 쓰기)"""
    f1, f2 = make_pair_key(file1, file2)
    cache_key = (f1, f2, method, hash_size)
    with compare_memory_lock:
        compare_memory_cache[cache_key] = bool(is_duplicate)
    schedule_compare_record(f1, f2, method, hash_size, is_duplicate)


# ============================================================
# 증분 비교 진행 상태 관리
# ============================================================
def get_processed_compare_files(method, hash_size):
    """DB에서 처리 완료된 파일 목록 조회"""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT path FROM compare_progress WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()


def update_processed_compare_files(method, hash_size, paths):
    """처리 완료된 파일 목록 저장 (DB 비동기 쓰기)"""
    for path in paths:
        if path:
            schedule_progress_record(method, hash_size, path)


# ============================================================
# 중복 결과 관리
# ============================================================
def record_duplicate_pair(f1, f2):
    """중복 쌍 기록 (정렬된 순서로 저장)"""
    with duplicates_lock:
        pair = tuple(sorted((f1, f2)))
        duplicate_pairs.add(pair)


def build_groups(pairs):
    """중복 쌍을 그룹으로 묶기 (연결 요소 찾기)"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)

    groups = {}
    for x in parent:
        r = find(x)
        groups.setdefault(r, set()).add(x)

    # parent에 없는 노드도 포함
    for a, b in pairs:
        if a not in parent:
            groups.setdefault(a, set()).add(a)
        if b not in parent:
            groups.setdefault(b, set()).add(b)

    return list(groups.values())


def get_duplicate_groups():
    """현재 중복 그룹 목록 반환"""
    with duplicates_lock:
        return build_groups(set(duplicate_pairs))


# ============================================================
# 중복 결과 DB 저장/로드
# ============================================================
def save_duplicate_results_to_db(method, hash_size):
    """중복 결과를 DB에 저장"""
    groups = get_duplicate_groups()
    if not groups:
        return

    # 기존 DB 결과 삭제 후 재저장
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM duplicate_results WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            for group_id, group in enumerate(groups):
                for path in sorted(group):
                    cur.execute(
                        "REPLACE INTO duplicate_results (method, hash_size, group_id, path) VALUES (?,?,?,?)",
                        (method, hash_size, group_id, path)
                    )
            conn.commit()
        finally:
            conn.close()


def load_duplicate_results_from_db(method, hash_size):
    """DB에서 중복 결과 로드"""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT group_id, path FROM duplicate_results WHERE method=? AND hash_size=? ORDER BY group_id, path",
                (method, hash_size)
            )
            rows = cur.fetchall()
        finally:
            conn.close()

    if not rows:
        return None

    # 그룹별로 묶기
    groups = {}
    for group_id, path in rows:
        groups.setdefault(group_id, []).append(path)

    result = [paths for paths in groups.values() if len(paths) > 1]

    # 메모리 중복 쌍에 반영
    with duplicates_lock:
        duplicate_pairs.clear()
        for group in result:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    duplicate_pairs.add(tuple(sorted((group[i], group[j]))))

    return result


def remove_missing_files_from_cache(method, hash_size, missing_paths):
    """
    존재하지 않는 파일을 DB 캐시에서 제거.
    - hash_cache: 해당 파일 해시 제거
    - compare_cache: 해당 파일이 포함된 비교 쌍 제거
    - compare_progress: 해당 파일 진행 상태 제거
    - duplicate_results: 해당 파일이 포함된 그룹에서 제거
    """
    if not missing_paths:
        return

    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            for path in missing_paths:
                # 해시 캐시에서 제거
                cur.execute(
                    "DELETE FROM hash_cache WHERE path=? AND method=? AND hash_size=?",
                    (path, method, hash_size)
                )
                # 비교 캐시에서 제거 (file1 또는 file2에 포함된 쌍)
                cur.execute(
                    "DELETE FROM compare_cache WHERE (file1=? OR file2=?) AND method=? AND hash_size=?",
                    (path, path, method, hash_size)
                )
                # 진행 상태에서 제거
                cur.execute(
                    "DELETE FROM compare_progress WHERE path=? AND method=? AND hash_size=?",
                    (path, method, hash_size)
                )
                # 중복 결과에서 제거
                cur.execute(
                    "DELETE FROM duplicate_results WHERE path=? AND method=? AND hash_size=?",
                    (path, method, hash_size)
                )
            conn.commit()
        finally:
            conn.close()

    # 메모리 캐시에서도 제거
    with hash_memory_lock:
        for key in list(hash_memory_cache.keys()):
            if key[0] in missing_paths and key[1] == method and key[2] == hash_size:
                del hash_memory_cache[key]

    with compare_memory_lock:
        for key in list(compare_memory_cache.keys()):
            if (key[0] in missing_paths or key[1] in missing_paths) and key[2] == method and key[3] == hash_size:
                del compare_memory_cache[key]

    with duplicates_lock:
        missing_set = set(missing_paths)
        duplicate_pairs.difference_update(
            pair for pair in duplicate_pairs
            if pair[0] in missing_set or pair[1] in missing_set
        )


# ============================================================
# 해시 버킷 기반 후보 선별
# ============================================================
def hash_to_int(hash_value):
    """해시 문자열을 정수로 변환"""
    if hash_value is None:
        return None
    try:
        return int(str(hash_value), 16)
    except Exception:
        return None


def get_hash_prefix_bits(hash_size):
    """해시 접두사 비트 수 결정"""
    total_bits = hash_size * hash_size
    return min(16, max(4, total_bits // 4))


def hash_prefix_key(hash_value, prefix_bits):
    """해시의 접두사 키 계산"""
    if hash_value is None:
        return None
    hex_str = str(hash_value)
    total_bits = len(hex_str) * 4
    if prefix_bits >= total_bits:
        return hex_str
    try:
        int_value = int(hex_str, 16)
    except Exception:
        return None
    return int_value >> (total_bits - prefix_bits)


def build_hash_buckets(paths, hashes, prefix_bits):
    """해시 접두사 기반 버킷 생성"""
    buckets = {}
    for path in paths:
        prefix = hash_prefix_key(hashes.get(path), prefix_bits)
        if prefix is None:
            continue
        buckets.setdefault(prefix, []).append(path)
    return buckets


def collect_candidate_pairs(paths, block_index):
    """버킷 기반 후보 쌍 수집"""
    candidates = {path: set() for path in paths}
    for bucket_paths in block_index.values():
        if len(bucket_paths) < 2:
            continue
        for i in range(len(bucket_paths)):
            p1 = bucket_paths[i]
            for p2 in bucket_paths[i + 1:]:
                candidates[p1].add(p2)
                candidates[p2].add(p1)
    return candidates


def filter_batch_candidates(file1, batch, candidates):
    """후보 목록으로 배치 필터링"""
    if candidates is None:
        return batch
    file_candidates = candidates.get(file1)
    if not file_candidates:
        return []
    return [file2 for file2 in batch if file2 in file_candidates]


# ============================================================
# 파일 비교
# ============================================================
def compare_files(file1, file2, method, hash_size, tolerance, verbose=False, use_compare_cache=False, hashes=None):
    """
    두 파일 비교.
    - use_compare_cache: True면 기존 비교 결과 재사용
    - hashes: 미리 계산된 해시 딕셔너리
    """
    if is_stop_requested():
        return 0, False
    item_start = time.perf_counter()

    # 기존 비교 결과 확인
    if use_compare_cache:
        cached_result = already_compared(file1, file2, method, hash_size)
        if cached_result is not None:
            item_elapsed = (time.perf_counter() - item_start) * 1000
            if cached_result:
                logger.info(f"  [bold green][유사 발견][/bold green] [cyan]{file1}[/cyan] == [cyan]{file2}[/cyan] (캐시됨, {item_elapsed:.2f}ms)")
            elif verbose:
                logger.info(f"  [dim][건별 비교][/dim] {os.path.basename(file1)} ↔ {os.path.basename(file2)}: [yellow]{item_elapsed:.2f}ms[/yellow] [dim](캐시됨)[/dim]")
            return item_elapsed, cached_result

    # 해시값 얻기
    if hashes is not None:
        h1 = hashes.get(file1)
        if h1 is None:
            h1 = get_cached_file_hash((file1, method, hash_size))
        h2 = hashes.get(file2)
        if h2 is None:
            h2 = get_cached_file_hash((file2, method, hash_size))
    else:
        h1 = get_cached_file_hash((file1, method, hash_size))
        h2 = get_cached_file_hash((file2, method, hash_size))

    if h1 is None or h2 is None:
        item_elapsed = (time.perf_counter() - item_start) * 1000
        return item_elapsed, False

    diff = h1 - h2
    is_duplicate = diff <= tolerance

    # 결과 저장
    if use_compare_cache:
        add_compare_record(file1, file2, method, hash_size, is_duplicate)

    item_elapsed = (time.perf_counter() - item_start) * 1000
    if is_duplicate:
        logger.info(f"  [bold green][유사 발견][/bold green] [cyan]{file1}[/cyan] == [cyan]{file2}[/cyan] (diff={diff}, {item_elapsed:.2f}ms)")
        try:
            record_duplicate_pair(file1, file2)
        except Exception:
            pass
    elif verbose:
        logger.info(f"  [dim][건별 비교][/dim] {os.path.basename(file1)} ↔ {os.path.basename(file2)}: [yellow]{item_elapsed:.2f}ms[/yellow]")

    return item_elapsed, is_duplicate


def _hash_str_to_int(hash_str):
    """해시 문자열을 정수로 변환 (메모리 캐시 사용)"""
    import threading
    global _hash_int_cache, _hash_int_lock
    with _hash_int_lock:
        if hash_str in _hash_int_cache:
            return _hash_int_cache[hash_str]
    try:
        val = int(str(hash_str), 16)
    except Exception:
        val = None
    with _hash_int_lock:
        _hash_int_cache[hash_str] = val
    return val


def preload_compare_cache(method, hash_size, max_memory_mb=0):
    """
    비교 캐시를 DB에서 메모리로 선로드.
    - max_memory_mb: 0이면 전체 로드, 0 초과면 해당 메모리(MB)만큼만 로드
    """
    global _compare_cache_loaded
    with _compare_cache_loaded_lock:
        if _compare_cache_loaded:
            return
        _compare_cache_loaded = True

    logger.info(f"[bold cyan][알림] 비교 캐시를 메모리에 선로드합니다. (max_memory_mb={max_memory_mb})[/bold cyan]")
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT file1, file2, is_duplicate FROM compare_cache WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            rows = cur.fetchall()
        finally:
            conn.close()

    loaded = 0
    with compare_memory_lock:
        for file1, file2, is_dup in rows:
            cache_key = (file1, file2, method, hash_size)
            compare_memory_cache[cache_key] = bool(is_dup)
            loaded += 1
            # max_memory_mb 제한: 대략 1건당 100바이트로 추정
            if max_memory_mb > 0 and loaded * 100 >= max_memory_mb * 1024 * 1024:
                break

    logger.info(f"[bold cyan][알림] 비교 캐시 {loaded:,}건을 메모리에 로드했습니다.[/bold cyan]")


def compare_file_with_list(file1, file2_list, method, hash_size, tolerance, verbose=False, use_compare_cache=False, duplicate_limit=0, hashes=None):
    """
    한 파일을 여러 파일과 비교.
    int.bit_count() 기반 해밍 거리 계산으로 빠르게 비교.
    - duplicate_limit: 중복 n건 도달 시 중단
    """
    if is_stop_requested() or not file2_list:
        return 0, 0

    if hashes is not None:
        h1 = hashes.get(file1)
        if h1 is None:
            h1 = get_cached_file_hash((file1, method, hash_size))
    else:
        h1 = get_cached_file_hash((file1, method, hash_size))
    if h1 is None:
        return 0, 0

    # file1의 해시를 정수로 변환
    h1_int = _hash_str_to_int(h1)
    if h1_int is None:
        return 0, 0

    total = 0
    duplicates = 0
    for file2 in file2_list:
        if is_stop_requested():
            break

        if hashes is not None:
            h2 = hashes.get(file2)
            if h2 is None:
                h2 = get_cached_file_hash((file2, method, hash_size))
        else:
            h2 = get_cached_file_hash((file2, method, hash_size))
        if h2 is None:
            continue

        # 기존 비교 결과 확인 (메모리 캐시 우선)
        if use_compare_cache:
            cached_result = already_compared(file1, file2, method, hash_size)
            if cached_result is not None:
                total += 1
                if cached_result:
                    duplicates += 1
                    try:
                        record_duplicate_pair(file1, file2)
                    except Exception:
                        pass
                    if duplicate_limit > 0 and duplicates >= duplicate_limit:
                        request_stop()
                        break
                continue

        # 해밍 거리 계산 (int.bit_count() - Python 3.10+)
        h2_int = _hash_str_to_int(h2)
        if h2_int is None:
            continue
        diff = h1_int ^ h2_int
        hamming_distance = diff.bit_count()
        is_duplicate = hamming_distance <= tolerance

        total += 1
        if is_duplicate:
            duplicates += 1
            try:
                record_duplicate_pair(file1, file2)
            except Exception:
                pass
            if duplicate_limit > 0 and duplicates >= duplicate_limit:
                request_stop()
                break

        # 비교 결과 저장 (메모리 캐시 + DB 비동기 배치)
        if use_compare_cache:
            add_compare_record(file1, file2, method, hash_size, is_duplicate)

    return total, duplicates


# ============================================================
# 비교 실행
# ============================================================
def _run_cross_folder_compare(folder_files, new_compare_files_set, hashes, candidates, method, hash_size, tolerance, duplicate_limit, use_compare_cache, start_time, log_interval, verbose_single):
    """cross_folder 모드 비교 실행"""
    total_compared = 0
    total_duplicates = 0
    total_pairs = 0
    keys = list(folder_files.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            f1, f2 = keys[i], keys[j]
            pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
            if pending_files:
                total_pairs += len(pending_files) * len(folder_files[f2])

    last_log_time = time.perf_counter()
    last_log_count = 0
    max_workers = _resolve_compare_workers()
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = set()

    def drain_futures(wait_all=False):
        nonlocal total_compared, total_duplicates, last_log_time, last_log_count
        if not futures:
            return False
        done, _ = wait(futures, return_when=ALL_COMPLETED if wait_all else FIRST_COMPLETED)
        stop_now = False
        for fut in done:
            futures.discard(fut)
            if fut.cancelled():
                continue
            try:
                completed_pairs, is_duplicate = fut.result()
            except Exception:
                continue
            total_compared += completed_pairs
            if is_duplicate:
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    stop_now = True
            if total_compared % log_interval == 0 or total_compared == total_pairs:
                now = time.perf_counter()
                interval_time = now - last_log_time
                interval_count = total_compared - last_log_count
                total_elapsed = now - start_time
                speed = interval_count / interval_time if interval_time > 0 else 0
                percent = (total_compared / total_pairs * 100) if total_pairs > 0 else 100.0
                logger.info(
                    f"[bold cyan][진행 상황][/bold cyan] "
                    f"[yellow]{total_compared:,}[/yellow] / {total_pairs:,}회 ({percent:.1f}%) | "
                    f"중복 건수: {total_duplicates:,} | "
                    f"최근 {interval_count:,}개: {interval_time:.2f}초 ({speed:.0f}개/초) | "
                    f"전체 경과: {total_elapsed:.1f}초"
                )
                last_log_time = now
                last_log_count = total_compared
        return stop_now

    try:
        for i in range(len(keys)):
            if is_stop_requested():
                break
            for j in range(i + 1, len(keys)):
                if is_stop_requested():
                    break
                f1, f2 = keys[i], keys[j]
                pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
                for file1 in pending_files:
                    update_processed_compare_files(method, hash_size, [file1])
                    if is_stop_requested():
                        break
                    batch = []
                    for file2 in folder_files[f2]:
                        if is_stop_requested():
                            break
                        batch.append(file2)
                        if len(batch) >= max_workers * 4:
                            filtered = filter_batch_candidates(file1, batch, candidates)
                            if filtered:
                                futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                            batch = []
                    if batch:
                        filtered = filter_batch_candidates(file1, batch, candidates)
                        if filtered:
                            futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                    if len(futures) >= max_workers * 2 and drain_futures():
                        break
                if is_stop_requested():
                    break
            if is_stop_requested():
                break
        if not is_stop_requested():
            drain_futures(wait_all=True)
    finally:
        for fut in futures:
            fut.cancel()
        executor.shutdown(wait=False)

    return total_compared, total_duplicates, total_pairs


def _run_all_folder_compare(all_files, new_compare_files_set, hashes, candidates, method, hash_size, tolerance, duplicate_limit, use_compare_cache, start_time, log_interval, verbose_single):
    """all_folders 모드 비교 실행"""
    total_compared = 0
    total_duplicates = 0
    total_pairs = max(0, len([p for p in all_files if p in new_compare_files_set]) * (len(all_files) - 1))
    last_log_time = time.perf_counter()
    last_log_count = 0
    max_workers = _resolve_compare_workers()
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = set()

    def drain_futures(wait_all=False):
        nonlocal total_compared, total_duplicates, last_log_time, last_log_count
        if not futures:
            return False
        done, _ = wait(futures, return_when=ALL_COMPLETED if wait_all else FIRST_COMPLETED)
        stop_now = False
        for fut in done:
            futures.discard(fut)
            if fut.cancelled():
                continue
            try:
                completed_pairs, is_duplicate = fut.result()
            except Exception:
                continue
            total_compared += completed_pairs
            if is_duplicate:
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    stop_now = True
            if total_compared % log_interval == 0 or total_compared == total_pairs:
                now = time.perf_counter()
                interval_time = now - last_log_time
                interval_count = total_compared - last_log_count
                total_elapsed = now - start_time
                speed = interval_count / interval_time if interval_time > 0 else 0
                percent = (total_compared / total_pairs * 100) if total_pairs > 0 else 100.0
                logger.info(
                    f"[bold cyan][진행 상황][/bold cyan] "
                    f"[yellow]{total_compared:,}[/yellow] / {total_pairs:,}회 ({percent:.1f}%) | "
                    f"중복 건수: {total_duplicates:,} | "
                    f"최근 {interval_count:,}개: {interval_time:.2f}초 ({speed:.0f}개/초) | "
                    f"전체 경과: {total_elapsed:.1f}초"
                )
                last_log_time = now
                last_log_count = total_compared
        return stop_now

    try:
        for file1 in [p for p in all_files if p in new_compare_files_set]:
            if is_stop_requested():
                break
            update_processed_compare_files(method, hash_size, [file1])
            batch = []
            for file2 in all_files:
                if file2 == file1:
                    continue
                if is_stop_requested():
                    break
                batch.append(file2)
                if len(batch) >= max_workers * 4:
                    filtered = filter_batch_candidates(file1, batch, candidates)
                    if filtered:
                        futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                    batch = []
            if batch:
                filtered = filter_batch_candidates(file1, batch, candidates)
                if filtered:
                    futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
            if len(futures) >= max_workers * 2 and drain_futures():
                break
        if not is_stop_requested():
            drain_futures(wait_all=True)
    finally:
        for fut in futures:
            fut.cancel()
        executor.shutdown(wait=False)

    return total_compared, total_duplicates, total_pairs