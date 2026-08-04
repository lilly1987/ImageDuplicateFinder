"""
해시 계산 모듈.

이미지 해시(aHash/pHash/dHash/wHash) 계산, 캐시 관리, 일괄 계산.
"""

import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

from PIL import Image
import imagehash
from logger import logger
from database import DB_FILE, DB_TIMEOUT, db_lock, schedule_hash_cache_write, start_db_writer
from state import hash_memory_cache, hash_memory_lock, is_stop_requested

# 해시 계산 프로세스 풀
hash_process_pool = None


# ============================================================
# 해시 계산
# ============================================================
def get_hash_process_pool():
    """해시 계산 프로세스 풀 생성/반환"""
    global hash_process_pool
    if hash_process_pool is None:
        max_workers = min(32, max(1, (os.cpu_count() or 4)))
        hash_process_pool = ProcessPoolExecutor(max_workers=max_workers)
    return hash_process_pool


def compute_hash_worker(path, method, hash_size):
    """단일 파일 해시 계산 (프로세스 풀에서 실행)"""
    try:
        img = Image.open(path)
        if method == "ahash":
            h = imagehash.average_hash(img, hash_size=hash_size)
        elif method == "phash":
            h = imagehash.phash(img, hash_size=hash_size)
        elif method == "dhash":
            h = imagehash.dhash(img, hash_size=hash_size)
        elif method == "whash":
            h = imagehash.whash(img, hash_size=hash_size)
        else:
            return None
        return str(h)
    except Exception:
        return None


# ============================================================
# 해시 캐시 관리
# ============================================================
def get_file_hash(path, method="ahash", hash_size=8):
    """파일 해시 조회 (DB 캐시 → 계산 → 메모리 캐시)"""
    if not os.path.isfile(path):
        return None
    try:
        stat = os.stat(path)
    except Exception:
        return None

    # DB에서 해시 확인
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT hash, mtime, size FROM hash_cache WHERE path=? AND method=? AND hash_size=?",
                (path, method, hash_size)
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
            hash_text = future.result(timeout=0.5)
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

    if hash_text is None:
        return None

    try:
        h = imagehash.hex_to_hash(hash_text)
    except Exception:
        return None

    schedule_hash_cache_write(path, method, hash_size, hash_text, stat.st_mtime, stat.st_size)
    return h


def get_cached_file_hash(key):
    """메모리 캐시 → DB 캐시 → 계산 순으로 해시 조회"""
    with hash_memory_lock:
        if key in hash_memory_cache:
            return hash_memory_cache[key]

    path, method, hash_size = key
    h = get_file_hash(path, method, hash_size)

    with hash_memory_lock:
        hash_memory_cache[key] = h

    return h


def _query_cached_hashes(paths, method, hash_size):
    """DB에서 여러 파일의 해시 일괄 조회 (유효한 것만)"""
    if not paths:
        return {}

    cached = {}
    chunk_size = 200
    for i in range(0, len(paths), chunk_size):
        chunk = paths[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT path, hash, mtime, size FROM hash_cache WHERE method=? AND hash_size=? AND path IN ({placeholders})",
                    (method, hash_size, *chunk)
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

    # DB 캐시에서 조회
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
                futures = {
                    process_pool.submit(compute_hash_worker, path, method, hash_size): path
                    for path in batch_paths
                }
                pending = set(futures)
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
                        except Exception:
                            hash_text = None

                        if hash_text is None:
                            with hash_memory_lock:
                                hash_memory_cache[(path, method, hash_size)] = None
                            hashes[path] = None
                        else:
                            try:
                                h = imagehash.hex_to_hash(hash_text)
                            except Exception:
                                with hash_memory_lock:
                                    hash_memory_cache[(path, method, hash_size)] = None
                                hashes[path] = None
                            else:
                                hashes[path] = h
                                with hash_memory_lock:
                                    hash_memory_cache[(path, method, hash_size)] = h
                                try:
                                    stat = os.stat(path)
                                    schedule_hash_cache_write(path, method, hash_size, hash_text, stat.st_mtime, stat.st_size)
                                except Exception:
                                    pass

                        if is_stop_requested():
                            for fut in pending:
                                try:
                                    fut.cancel()
                                except Exception:
                                    pass
                            pending.clear()
                            break

                elapsed = time.perf_counter() - chunk_start
                logger.info(f"[bold cyan][알림] batch {start // chunk_size + 1} 완료: {len(batch_paths)}개, 소요 {elapsed:.2f}초[/bold cyan]")

    return hashes