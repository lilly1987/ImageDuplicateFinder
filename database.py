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


# ============================================================
# DB 초기화 및 스키마
# ============================================================
def init_db():
    """DB 테이블 생성 (없으면 생성) 및 기존 스키마 마이그레이션"""
    with db_lock:
        conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
        try:
            # WAL 모드 활성화: 강제 종료 시 데이터 손상 방지 + 읽기/쓰기 동시성 향상
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            # 해시 캐시: 파일 경로 + 알고리즘 + 해시 크기별 해시값
            cur.execute("""CREATE TABLE IF NOT EXISTS hash_cache (
                path TEXT,
                method TEXT,
                hash_size INTEGER,
                hash TEXT,
                mtime INTEGER,
                size INTEGER,
                PRIMARY KEY (path, method, hash_size)
            )""")
            # 비교 결과 캐시: 파일 쌍별 중복 여부 (정렬된 순서로 저장)
            cur.execute("""CREATE TABLE IF NOT EXISTS compare_cache (
                file1 TEXT,
                file2 TEXT,
                method TEXT,
                hash_size INTEGER,
                is_duplicate INTEGER,
                PRIMARY KEY (file1, file2, method, hash_size)
            )""")
            # 증분 비교 진행 상태: 처리 완료된 파일 목록
            cur.execute("""CREATE TABLE IF NOT EXISTS compare_progress (
                method TEXT,
                hash_size INTEGER,
                path TEXT,
                PRIMARY KEY (method, hash_size, path)
            )""")
            # 중복 결과 그룹: 그룹 ID별 파일 목록
            cur.execute("""CREATE TABLE IF NOT EXISTS duplicate_results (
                method TEXT,
                hash_size INTEGER,
                group_id INTEGER,
                path TEXT,
                PRIMARY KEY (method, hash_size, group_id, path)
            )""")

            # 기존 스키마 마이그레이션:
            # 이전 버전의 compare_cache는 tolerance_rate 컬럼을 사용했음.
            # 새 버전은 is_duplicate 컬럼을 사용하므로, 기존 데이터를 변환한다.
            cur.execute("PRAGMA table_info(compare_cache)")
            columns = [row[1] for row in cur.fetchall()]
            if "tolerance_rate" in columns and "is_duplicate" not in columns:
                logger.info("[bold cyan][알림] 기존 compare_cache 스키마를 새 버전으로 마이그레이션합니다.[/bold cyan]")
                # tolerance_rate <= 0.0 이면 중복으로 간주 (기존 로직과 동일)
                cur.execute("""ALTER TABLE compare_cache ADD COLUMN is_duplicate INTEGER DEFAULT 0""")
                cur.execute("""
                    UPDATE compare_cache
                    SET is_duplicate = CASE WHEN tolerance_rate <= 0.0 THEN 1 ELSE 0 END
                """)
                conn.commit()

            conn.commit()
        finally:
            conn.close()


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
    """큐에 쌓인 쓰기 작업을 DB에 일괄 반영"""
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
            if hash_rows:
                cur.executemany(
                    "REPLACE INTO hash_cache (path, method, hash_size, hash, mtime, size) VALUES (?,?,?,?,?,?)",
                    hash_rows
                )
            if compare_rows:
                cur.executemany(
                    "REPLACE INTO compare_cache (file1, file2, method, hash_size, is_duplicate) VALUES (?,?,?,?,?)",
                    compare_rows
                )
            if progress_rows:
                cur.executemany(
                    "REPLACE INTO compare_progress (method, hash_size, path) VALUES (?,?,?)",
                    progress_rows
                )
            if duplicate_rows:
                cur.executemany(
                    "REPLACE INTO duplicate_results (method, hash_size, group_id, path) VALUES (?,?,?,?)",
                    duplicate_rows
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