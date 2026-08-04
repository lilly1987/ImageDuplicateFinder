"""
데이터베이스 모듈.

SQLite 연결, WAL 모드 설정, 비동기 쓰기 스레드, 스키마 관리.
"""

import os
import sqlite3
import threading
import atexit

from logger import logger

# ============================================================
# 상수 및 전역 상태
# ============================================================
DB_FILE = "cache.db"
DB_TIMEOUT = 30  # sqlite3 lock 대기 시간 (초)

# DB 접근 락
db_lock = threading.Lock()

# DB 비동기 쓰기 큐
db_write_lock = threading.Lock()
db_write_event = threading.Event()
db_write_stop = threading.Event()
hash_write_queue = []
compare_write_queue = []
progress_write_queue = []
duplicate_write_queue = []
db_write_thread = None
DB_WRITE_FLUSH_INTERVAL = 2.0
DB_WRITE_BATCH_SIZE = 1000


def _table_name(base, method, hash_size):
    """(method, hash_size)별 테이블명 생성: hash_cache_ahash_8"""
    safe_method = method.replace("-", "_").replace(" ", "_")
    return f"{base}_{safe_method}_{hash_size}"


def set_db_write_options(flush_interval=None, batch_size=None):
    """DB 비동기 쓰기 옵션 설정 (config.yml에서 호출)"""
    global DB_WRITE_FLUSH_INTERVAL, DB_WRITE_BATCH_SIZE
    if flush_interval is not None:
        try:
            flush_interval = float(flush_interval)
            if flush_interval > 0:
                DB_WRITE_FLUSH_INTERVAL = flush_interval
        except Exception:
            pass
    if batch_size is not None:
        try:
            batch_size = int(batch_size)
            if batch_size > 0:
                DB_WRITE_BATCH_SIZE = batch_size
        except Exception:
            pass


# ============================================================
# DB 초기화 및 스키마
# ============================================================
def init_db():
    """DB 테이블 생성 (없으면 생성) - (method, hash_size)별 개별 테이블"""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            # WAL 모드 활성화: 강제 종료 시 데이터 손상 방지 + 읽기/쓰기 동시성 향상
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")

            # 기존 단일 테이블에서 데이터 마이그레이션 (한 번만 실행)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hash_cache'")
            if cur.fetchone():
                logger.info("[bold cyan][알림] 기존 단일 테이블을 (method, hash_size)별 테이블로 마이그레이션합니다.[/bold cyan]")
                _migrate_single_tables_to_per_method(cur, conn)

            conn.commit()
        finally:
            conn.close()


def _migrate_single_tables_to_per_method(cur, conn):
    """기존 단일 테이블에서 (method, hash_size)별 테이블로 데이터 이동"""
    # 기존 테이블에서 (method, hash_size) 조합 추출
    cur.execute("SELECT DISTINCT method, hash_size FROM hash_cache")
    combos = cur.fetchall()
    for method, hash_size in combos:
        _ensure_tables_exist(cur, method, hash_size)
        # 데이터 복사
        cur.execute(
            "SELECT path, hash, mtime, size FROM hash_cache WHERE method=? AND hash_size=?",
            (method, hash_size)
        )
        for path, hash_text, mtime, size in cur.fetchall():
            cur.execute(
                f"INSERT OR REPLACE INTO {_table_name('hash_cache', method, hash_size)} (path, hash, mtime, size) VALUES (?,?,?,?)",
                (path, hash_text, mtime, size)
            )
        cur.execute(
            "SELECT file1, file2, is_duplicate FROM compare_cache WHERE method=? AND hash_size=?",
            (method, hash_size)
        )
        for file1, file2, is_dup in cur.fetchall():
            cur.execute(
                f"INSERT OR REPLACE INTO {_table_name('compare_cache', method, hash_size)} (file1, file2, is_duplicate) VALUES (?,?,?)",
                (file1, file2, is_dup)
            )
        cur.execute(
            "SELECT path FROM compare_progress WHERE method=? AND hash_size=?",
            (method, hash_size)
        )
        for (path,) in cur.fetchall():
            cur.execute(
                f"INSERT OR REPLACE INTO {_table_name('compare_progress', method, hash_size)} (path) VALUES (?)",
                (path,)
            )
        cur.execute(
            "SELECT group_id, path FROM duplicate_results WHERE method=? AND hash_size=?",
            (method, hash_size)
        )
        for group_id, path in cur.fetchall():
            cur.execute(
                f"INSERT OR REPLACE INTO {_table_name('duplicate_results', method, hash_size)} (group_id, path) VALUES (?,?)",
                (group_id, path)
            )
    # 기존 테이블 삭제
    cur.execute("DROP TABLE IF EXISTS hash_cache")
    cur.execute("DROP TABLE IF EXISTS compare_cache")
    cur.execute("DROP TABLE IF EXISTS compare_progress")
    cur.execute("DROP TABLE IF EXISTS duplicate_results")
    conn.commit()
    logger.info(f"[bold cyan][알림] 마이그레이션 완료: {len(combos)}개 (method, hash_size) 조합[/bold cyan]")


def _ensure_tables_exist(cur, method, hash_size):
    """(method, hash_size)별 테이블 생성 (없으면 생성)"""
    hash_table = _table_name("hash_cache", method, hash_size)
    compare_table = _table_name("compare_cache", method, hash_size)
    progress_table = _table_name("compare_progress", method, hash_size)
    results_table = _table_name("duplicate_results", method, hash_size)

    cur.execute(f"""CREATE TABLE IF NOT EXISTS {hash_table} (
        path TEXT PRIMARY KEY,
        hash TEXT,
        mtime INTEGER,
        size INTEGER
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {compare_table} (
        file1 TEXT,
        file2 TEXT,
        is_duplicate INTEGER,
        PRIMARY KEY (file1, file2)
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {progress_table} (
        path TEXT PRIMARY KEY
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {results_table} (
        group_id INTEGER,
        path TEXT,
        PRIMARY KEY (group_id, path)
    )""")


# ============================================================
# DB 비동기 쓰기 스레드
# ============================================================
def start_db_writer():
    """DB 비동기 쓰기 스레드 시작"""
    global db_write_thread
    if db_write_thread is None:
        db_write_thread = threading.Thread(target=_db_writer_loop, daemon=True)
        db_write_thread.start()


def _db_writer_loop():
    """DB 쓰기 루프: 주기적으로 큐를 비워 DB에 반영"""
    while not db_write_stop.is_set():
        db_write_event.wait(timeout=DB_WRITE_FLUSH_INTERVAL)
        db_write_event.clear()
        _flush_db_writes()
    _flush_db_writes()


def _flush_db_writes():
    """큐에 쌓인 쓰기 작업을 DB에 일괄 반영 (method, hash_size별 테이블 사용)"""
    with db_write_lock:
        hash_rows = list(hash_write_queue)
        compare_rows = list(compare_write_queue)
        progress_rows = list(progress_write_queue)
        duplicate_rows = list(duplicate_write_queue)
        hash_write_queue.clear()
        compare_write_queue.clear()
        progress_write_queue.clear()
        duplicate_write_queue.clear()

    if not (hash_rows or compare_rows or progress_rows or duplicate_rows):
        return

    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            cur = conn.cursor()

            # hash_cache: (method, hash_size)별 테이블에 쓰기
            if hash_rows:
                hash_groups = {}
                for path, method, hash_size, hash_text, mtime, size in hash_rows:
                    key = (method, hash_size)
                    hash_groups.setdefault(key, []).append((path, hash_text, mtime, size))
                for (method, hash_size), rows in hash_groups.items():
                    _ensure_tables_exist(cur, method, hash_size)
                    table = _table_name("hash_cache", method, hash_size)
                    cur.executemany(
                        f"REPLACE INTO {table} (path, hash, mtime, size) VALUES (?,?,?,?)",
                        rows
                    )

            # compare_cache: (method, hash_size)별 테이블에 쓰기
            if compare_rows:
                compare_groups = {}
                for file1, file2, method, hash_size, is_dup in compare_rows:
                    key = (method, hash_size)
                    compare_groups.setdefault(key, []).append((file1, file2, int(is_dup)))
                for (method, hash_size), rows in compare_groups.items():
                    _ensure_tables_exist(cur, method, hash_size)
                    table = _table_name("compare_cache", method, hash_size)
                    cur.executemany(
                        f"REPLACE INTO {table} (file1, file2, is_duplicate) VALUES (?,?,?)",
                        rows
                    )

            # compare_progress: (method, hash_size)별 테이블에 쓰기
            if progress_rows:
                progress_groups = {}
                for method, hash_size, path in progress_rows:
                    key = (method, hash_size)
                    progress_groups.setdefault(key, []).append((path,))
                for (method, hash_size), rows in progress_groups.items():
                    _ensure_tables_exist(cur, method, hash_size)
                    table = _table_name("compare_progress", method, hash_size)
                    cur.executemany(
                        f"REPLACE INTO {table} (path) VALUES (?)",
                        rows
                    )

            # duplicate_results: (method, hash_size)별 테이블에 쓰기
            if duplicate_rows:
                dup_groups = {}
                for method, hash_size, group_id, path in duplicate_rows:
                    key = (method, hash_size)
                    dup_groups.setdefault(key, []).append((group_id, path))
                for (method, hash_size), rows in dup_groups.items():
                    _ensure_tables_exist(cur, method, hash_size)
                    table = _table_name("duplicate_results", method, hash_size)
                    cur.executemany(
                        f"REPLACE INTO {table} (group_id, path) VALUES (?,?)",
                        rows
                    )

            conn.commit()
        finally:
            conn.close()


def schedule_hash_cache_write(path, method, hash_size, hash_text, mtime, size):
    """해시 캐시 쓰기 예약"""
    with db_write_lock:
        hash_write_queue.append((path, method, hash_size, hash_text, mtime, size))
        if len(hash_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def schedule_compare_record(file1, file2, method, hash_size, is_duplicate):
    """비교 결과 캐시 쓰기 예약"""
    with db_write_lock:
        compare_write_queue.append((file1, file2, method, hash_size, int(is_duplicate)))
        if len(compare_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def schedule_progress_record(method, hash_size, path):
    """비교 진행 상태 쓰기 예약"""
    with db_write_lock:
        progress_write_queue.append((method, hash_size, path))
        if len(progress_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def schedule_duplicate_record(method, hash_size, group_id, path):
    """중복 결과 그룹 쓰기 예약"""
    with db_write_lock:
        duplicate_write_queue.append((method, hash_size, group_id, path))
        if len(duplicate_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def stop_db_writer():
    """DB 쓰기 스레드 종료 및 남은 큐 데이터 저장"""
    db_write_stop.set()
    db_write_event.set()
    if db_write_thread is not None:
        db_write_thread.join(timeout=5)
    # 강제 종료 시에도 남은 큐 데이터를 DB에 저장
    try:
        _flush_db_writes()
    except Exception:
        pass


# 프로그램 종료/강제 종료 시 남은 캐시 데이터를 DB에 저장
atexit.register(stop_db_writer)