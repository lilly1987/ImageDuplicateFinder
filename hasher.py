"""
해시 계산 모듈.

이미지 해시(aHash/pHash/dHash/wHash) 계산, 캐시 관리, 일괄 계산.
"""

import os
import sqlite3
import threading
import time
import numpy as np
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool

from PIL import Image
import imagehash
from logger import logger
from database import DB_FILE, DB_TIMEOUT, db_lock, schedule_hash_cache_write, start_db_writer, _table_name, _ensure_tables_exist
from state import hash_memory_cache, hash_memory_lock, is_stop_requested, image_sizes, image_sizes_lock

# 해시 계산 프로세스 풀
hash_process_pool = None
_hash_worker_count = 0  # 0이면 CPU 코어 기반 자동

# imagehash 라이브러리는 hash_size^2 비트 해시를 생성함.
# 예: hash_size=64 → 4096비트(hex 1024자), 128 → 16384비트(hex 4096자), 256 → 65536비트(hex 16384자)
# 128/256은 파일당 2KB~8KB 문자열이 되어 DB가 수십 GB로 비대해지므로 최대 64로 제한.
MAX_HASH_SIZE = 64

# 해시 계산 전역 카운터 (자동 재시도용 전역 해시 예산 관리)
# - precompute_hashes가 실제로 계산한 파일 수(성공+실패)를 누적
# - collector.py의 hash_producer가 max_hash_compute_files 예산 차감에 사용
_hash_compute_count = 0
_hash_compute_count_lock = threading.Lock()


def get_hash_compute_count():
    """현재까지 새로 계산된 총 해시 수 반환 (전역 해시 예산 관리용)"""
    global _hash_compute_count
    with _hash_compute_count_lock:
        return _hash_compute_count


def reset_hash_compute_count():
    """해시 계산 카운터 초기화 (비교 시작 시 호출)"""
    global _hash_compute_count
    with _hash_compute_count_lock:
        _hash_compute_count = 0


def _increment_hash_compute_count(count=1):
    """해시 계산 카운터 증가 (스레드 안전)"""
    global _hash_compute_count
    with _hash_compute_count_lock:
        _hash_compute_count += count


def _clamp_hash_size(hash_size):
    """과대 해시 크기 방어: 1~64 범위로 제한 (0 이하 및 64 초과 방지)"""
    try:
        hs = int(hash_size)
    except Exception:
        return MAX_HASH_SIZE
    return max(1, min(hs, MAX_HASH_SIZE))


def set_hash_worker_count(count):
    """해시 계산 프로세스 갯수 설정 (0이면 CPU 코어 기반 자동)"""
    global _hash_worker_count
    _hash_worker_count = max(0, int(count))


def _create_hash_process_pool():
    """새 해시 계산 프로세스 풀 생성"""
    if _hash_worker_count > 0:
        max_workers = min(32, _hash_worker_count)
    else:
        cpu = os.cpu_count() or 4
        # UI 스레드 반응성을 위해 최소 1코어 남김
        max_workers = min(32, max(1, cpu - 1))
    return ProcessPoolExecutor(max_workers=max_workers)


def get_hash_process_pool():
    """해시 계산 프로세스 풀 생성/반환 (손상 시 자동 재생성)"""
    global hash_process_pool
    if hash_process_pool is None:
        hash_process_pool = _create_hash_process_pool()
    # 손상된 풀(BrokenProcessPool)은 재생성
    try:
        if getattr(hash_process_pool, "_broken", False):
            _reset_hash_process_pool()
    except Exception:
        pass
    return hash_process_pool


def _reset_hash_process_pool():
    """손상된 프로세스 풀을 재생성하고 새 풀 반환"""
    global hash_process_pool
    try:
        if hash_process_pool is not None:
            hash_process_pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    hash_process_pool = _create_hash_process_pool()
    return hash_process_pool


def _submit_hash_worker(process_pool, path, method, hash_size):
    """해시 워커 제출 (풀 손상 시 재생성 후 1회 재시도)"""
    try:
        return process_pool.submit(compute_hash_worker, path, method, hash_size)
    except Exception:
        process_pool = _reset_hash_process_pool()
        try:
            return process_pool.submit(compute_hash_worker, path, method, hash_size)
        except Exception:
            return None


def _compute_hash_batch(batch_paths, method, hash_size, process_pool):
    """
    배치 해시 계산 (풀 손상 시 None 반환).
    - 반환: {path: (hash_str, width, height) 또는 None}
    """
    try:
        futures = {
            process_pool.submit(compute_hash_worker, path, method, hash_size): path
            for path in batch_paths
        }
    except Exception:
        return None  # submit 실패 (풀 손상)

    pending = set(futures)
    results = {}
    while pending:
        if is_stop_requested():
            for fut in pending:
                try:
                    fut.cancel()
                except Exception:
                    pass
            break

        done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
        if not done:
            continue

        for future in done:
            path = futures[future]
            try:
                hash_text = future.result()
            except BrokenProcessPool:
                return None  # 풀 손상 - 호출 측에서 재생성 후 재시도
            except Exception:
                hash_text = None
            results[path] = hash_text

        if is_stop_requested():
            for fut in pending:
                try:
                    fut.cancel()
                except Exception:
                    pass
            pending.clear()
            break

    return results


def _clamp_hash_size_for_worker(hash_size):
    """프로세스 풀 워커에서 사용할 해시 크기 클램프 (멀티프로세싱 안전)"""
    return _clamp_hash_size(hash_size)


def compute_hash_worker(path, method, hash_size):
    """
    단일 파일 해시 계산 (프로세스 풀에서 실행).
    - 반환: (hash_str, width, height) 또는 None
    - width/height는 해상도 비율(aspect_ratio_tol) 필터링에 사용.
    """
    hash_size = _clamp_hash_size_for_worker(hash_size)
    try:
        img = Image.open(path)
        width, height = img.size
        if method == "ahash":
            h = imagehash.average_hash(img, hash_size=hash_size)
        elif method == "phash":
            h = imagehash.phash(img, hash_size=hash_size)
        elif method == "dhash":
            h = imagehash.dhash(img, hash_size=hash_size)
        elif method == "whash":
            h = imagehash.whash(img, hash_size=hash_size)
        elif method == "bhash":
            h = block_hash(img, hash_size=hash_size)
        else:
            return None
        return str(h), width, height
    except Exception:
        return None


def block_hash(img, hash_size=16):
    """
    블록 해시 (bHash).
    - 이미지를 hash_size x hash_size 블록으로 나누어 각 블록의 평균 밝기 계산
    - 전체 평균 밝기와 비교하여 이진 해시 생성
    - all dup 프로그램의 bhash와 유사한 방식
    - hash_size는 최대 64로 제한 (64x64 = 4096비트 해시)
    """
    hash_size = _clamp_hash_size(hash_size)
    # 회색조로 변환
    img = img.convert("L")
    # hash_size x hash_size로 리사이즈
    img = img.resize((hash_size, hash_size))
    # 픽셀 데이터를 numpy 배열로 변환
    arr = np.array(img, dtype=np.float64)
    # 전체 평균 밝기
    avg = arr.mean()
    # 이진 해시: 각 블록이 평균보다 밝으면 1, 어두우면 0
    hash_array = (arr > avg).astype(int)
    return imagehash.ImageHash(hash_array)


# ============================================================
# 해시 캐시 관리
# ============================================================
def get_file_hash(path, method="ahash", hash_size=8):
    """파일 해시 조회 (DB 캐시 → 계산 → 메모리 캐시)"""
    hash_size = _clamp_hash_size(hash_size)
    if not os.path.isfile(path):
        return None
    try:
        stat = os.stat(path)
    except Exception:
        return None

    # DB에서 해시 확인 (method, hash_size별 테이블)
    hash_table = _table_name("hash_cache", method, hash_size)
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            _ensure_tables_exist(cur, method, hash_size)
            cur.execute(
                f"SELECT hash, mtime, size FROM {hash_table} WHERE path=?",
                (path,)
            )
            row = cur.fetchone()
        finally:
            conn.close()

    # 캐시가 유효하면 (mtime, size 동일) 반환
    if row and row[1] == stat.st_mtime and row[2] == stat.st_size:
        try:
            return imagehash.hex_to_hash(row[0])
        except Exception:
            pass

    if is_stop_requested():
        return None

    # 해시 계산
    process_pool = get_hash_process_pool()
    future = process_pool.submit(compute_hash_worker, path, method, hash_size)
    while True:
        try:
            result = future.result(timeout=0.5)
            break
        except TimeoutError:
            if is_stop_requested():
                try:
                    future.cancel()
                except Exception:
                    pass
                return None
            continue
        except KeyboardInterrupt:
            from state import request_stop
            request_stop()
            try:
                future.cancel()
            except Exception:
                pass
            return None

    if result is None:
        return None

    # 반환값: (hash_str, width, height)
    if isinstance(result, tuple):
        hash_text, width, height = result
    else:
        # 하위 호환: 구버전 워커 반환
        hash_text = result
        width, height = None, None

    try:
        h = imagehash.hex_to_hash(hash_text)
    except Exception:
        return None

    # 이미지 크기 저장 (해상도 비율 필터링용)
    if width and height:
        with image_sizes_lock:
            image_sizes[path] = (width, height)

    schedule_hash_cache_write(path, method, hash_size, hash_text, stat.st_mtime, stat.st_size)
    return h


def get_cached_file_hash(key):
    """메모리 캐시 → DB 캐시 → 계산 순으로 해시 조회"""
    with hash_memory_lock:
        if key in hash_memory_cache:
            return hash_memory_cache[key]

    path, method, hash_size = key
    hash_size = _clamp_hash_size(hash_size)
    key = (path, method, hash_size)
    h = get_file_hash(path, method, hash_size)

    with hash_memory_lock:
        hash_memory_cache[key] = h

    return h


def _query_cached_hashes(paths, method, hash_size):
    """DB에서 여러 파일의 해시 일괄 조회 (유효한 것만)"""
    if not paths:
        return {}

    hash_size = _clamp_hash_size(hash_size)
    cached = {}
    chunk_size = 200
    hash_table = _table_name("hash_cache", method, hash_size)
    for i in range(0, len(paths), chunk_size):
        chunk = paths[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
            try:
                cur = conn.cursor()
                _ensure_tables_exist(cur, method, hash_size)
                cur.execute(
                    f"SELECT path, hash, mtime, size FROM {hash_table} WHERE path IN ({placeholders})",
                    (*chunk,)
                )
                rows = cur.fetchall()
            finally:
                conn.close()

        for path, hash_text, mtime, size in rows:
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except Exception:
                continue
            if stat.st_mtime == mtime and stat.st_size == size:
                try:
                    cached[path] = imagehash.hex_to_hash(hash_text)
                except Exception:
                    pass

    return cached


def precompute_hashes(paths, method, hash_size, batch_size=1000, max_new_hashes=0, previous_paths=None):
    """
    여러 파일의 해시를 일괄 계산.
    - batch_size: 한 번에 계산할 파일 수
    - max_new_hashes: 0 초과 시 기존 계산된 건 제외하고 추가로 계산할 해시 갯수
    - previous_paths: 이전에 처리된 파일 목록 (증분 비교용)
    """
    hash_size = _clamp_hash_size(hash_size)
    unique_paths = list(dict.fromkeys(paths))
    hashes = {}
    missing = []

    # 메모리 캐시에서 조회
    with hash_memory_lock:
        for path in unique_paths:
            key = (path, method, hash_size)
            if key in hash_memory_cache:
                hashes[path] = hash_memory_cache[key]
            else:
                missing.append(path)

    # 증분 비교: 이전에 처리된 파일은 제외하고 신규 파일만 해시 계산
    if previous_paths is not None:
        previous_set = {p for p in previous_paths if p}
        missing = [p for p in missing if p not in previous_set]
        logger.info(f"[bold cyan][알림] 증분 비교 기준으로 새 해시 계산 대상 {len(missing)}개만 선택했습니다.[/bold cyan]")

    # DB 캐시에서 조회 (method, hash_size별 테이블)
    if missing:
        db_hashes = _query_cached_hashes(missing, method, hash_size)
        with hash_memory_lock:
            for path, h in db_hashes.items():
                key = (path, method, hash_size)
                hash_memory_cache[key] = h
                hashes[path] = h
        logger.info(f"[bold cyan][알림] DB에서 {len(db_hashes)}개의 해시를 가져왔습니다.[/bold cyan]")

        missing = [p for p in missing if p not in db_hashes]

        # max_new_hashes 제한 적용
        if max_new_hashes > 0:
            if len(missing) >= max_new_hashes:
                logger.info(f"[bold cyan][알림] 설정된 새 해시 계산 수: {max_new_hashes}개를 실행합니다.[/bold cyan]")
                missing = missing[:max_new_hashes]
            else:
                logger.info(f"[bold cyan][알림] 남은 신규 대상이 {len(missing)}개라서 모두 계산합니다.[/bold cyan]")

        logger.info(
            f"[bold cyan][알림] 새 해시 계산 대상: {len(missing)}개 "
            f"(설정값={max_new_hashes if max_new_hashes > 0 else '무제한'})[/bold cyan]"
        )

        # 배치 단위로 해시 계산
        if missing:
            process_pool = get_hash_process_pool()
            chunk_size = max(1, int(batch_size))
            for start in range(0, len(missing), chunk_size):
                if is_stop_requested():
                    break

                chunk_start = time.perf_counter()
                batch_paths = missing[start:start + chunk_size]
                # 풀 손상(BrokenProcessPool) 시 재생성 후 1회 재시도
                batch_results = _compute_hash_batch(batch_paths, method, hash_size, process_pool)
                if batch_results is None:
                    logger.warning("[bold yellow][알림] 해시 프로세스 풀이 손상되어 재생성 후 재시도합니다.[/bold yellow]")
                    process_pool = _reset_hash_process_pool()
                    batch_results = _compute_hash_batch(batch_paths, method, hash_size, process_pool)
                    if batch_results is None:
                        logger.error("[bold red][오류] 해시 프로세스 풀 재생성에도 실패하여 배치를 건너뜁니다.[/bold red]")
                        continue

                for path in batch_paths:
                    # 실제 해시 계산 시도 시 전역 카운트 증가 (성공+실패 모두)
                    _increment_hash_compute_count(1)
                    hash_text = batch_results.get(path)
                    if hash_text is None:
                        with hash_memory_lock:
                            hash_memory_cache[(path, method, hash_size)] = None
                        hashes[path] = None
                    else:
                        # 반환값: (hash_str, width, height)
                        if isinstance(hash_text, tuple):
                            hash_str_value, width, height = hash_text
                        else:
                            # 하위 호환: 구버전 워커 반환
                            hash_str_value = hash_text
                            width, height = None, None

                        try:
                            h = imagehash.hex_to_hash(hash_str_value)
                        except Exception:
                            with hash_memory_lock:
                                hash_memory_cache[(path, method, hash_size)] = None
                            hashes[path] = None
                        else:
                            hashes[path] = h
                            with hash_memory_lock:
                                hash_memory_cache[(path, method, hash_size)] = h
                            # 이미지 크기 저장 (해상도 비율 필터링용)
                            if width and height:
                                with image_sizes_lock:
                                    image_sizes[path] = (width, height)
                            try:
                                stat = os.stat(path)
                                schedule_hash_cache_write(path, method, hash_size, hash_str_value, stat.st_mtime, stat.st_size)
                            except Exception:
                                pass

                elapsed = time.perf_counter() - chunk_start
                logger.info(f"[bold cyan][알림] batch {start // chunk_size + 1} 완료: {len(batch_paths)}개, 소요 {elapsed:.2f}초[/bold cyan]")

    return hashes