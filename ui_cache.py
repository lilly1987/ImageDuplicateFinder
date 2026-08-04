"""
UI 캐시 관리 모듈.

DB 캐시 건수 조회, 캐시 초기화, DB 파일 삭제.
(method, hash_size)별 개별 테이블을 지원.
"""

import os
import sqlite3
from tkinter import messagebox
from compare import DB_FILE
from database import _table_name, _ensure_tables_exist, db_lock
from logger import logger


def _get_all_table_names(cur, base_name):
    """특정 베이스 이름(prefix)으로 시작하는 모든 테이블명 조회"""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
        (f"{base_name}_%",)
    )
    return [row[0] for row in cur.fetchall()]


def get_cache_counts(method=None, hash_size=None):
    """캐시 건수 조회 (해시, 비교, 진행 상태, 중복 결과)"""
    if not os.path.exists(DB_FILE):
        return 0, 0, 0, 0
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=30)
            cur = conn.cursor()

            if method is None or hash_size is None:
                # 모든 (method, hash_size) 테이블의 합계 조회
                h_cnt = 0
                c_cnt = 0
                p_cnt = 0
                d_cnt = 0
                for base in ("hash_cache", "compare_cache", "compare_progress", "duplicate_results"):
                    tables = _get_all_table_names(cur, base)
                    for table in tables:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cur.fetchone()[0]
                        if base == "hash_cache":
                            h_cnt += count
                        elif base == "compare_cache":
                            c_cnt += count
                        elif base == "compare_progress":
                            p_cnt += count
                        elif base == "duplicate_results":
                            d_cnt += count
            else:
                # 특정 (method, hash_size) 테이블 조회
                _ensure_tables_exist(cur, method, hash_size)
                h_table = _table_name("hash_cache", method, hash_size)
                c_table = _table_name("compare_cache", method, hash_size)
                p_table = _table_name("compare_progress", method, hash_size)
                d_table = _table_name("duplicate_results", method, hash_size)

                cur.execute(f"SELECT COUNT(*) FROM {h_table}")
                h_cnt = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM {c_table}")
                c_cnt = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM {p_table}")
                p_cnt = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM {d_table}")
                d_cnt = cur.fetchone()[0]

            conn.close()
            return h_cnt, c_cnt, p_cnt, d_cnt
    except Exception:
        return 0, 0, 0, 0


def clear_cache(update_fn=None):
    """캐시 데이터 초기화 (모든 (method, hash_size) 테이블)"""
    if not messagebox.askyesno("캐시 초기화 확인", "정말로 캐시 데이터를 초기화하시겠습니까?", default=messagebox.NO):
        return

    if os.path.exists(DB_FILE):
        try:
            with db_lock:
                conn = sqlite3.connect(DB_FILE, timeout=30)
                cur = conn.cursor()
                # 모든 캐시 테이블의 데이터 삭제
                for base in ("hash_cache", "compare_cache", "compare_progress", "duplicate_results"):
                    tables = _get_all_table_names(cur, base)
                    for table in tables:
                        cur.execute(f"DELETE FROM {table}")
                conn.commit()
                conn.close()
            logger.info("[bold green][알림] 캐시 데이터가 초기화되었습니다.[/bold green]")
        except Exception as e:
            logger.error(f"[bold red][오류] 캐시 초기화 실패: {e}[/bold red]")
    if update_fn:
        update_fn()


def drop_db(update_fn=None):
    """DB 파일 삭제"""
    if not messagebox.askyesno("DB 삭제 확인", "정말로 캐시 DB 파일(cache.db)을 삭제하시겠습니까?", default=messagebox.NO):
        return

    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
            logger.info("[bold green][알림] DB 파일(cache.db)이 삭제되었습니다.[/bold green]")
        except Exception as e:
            logger.error(f"[bold red][오류] DB 삭제 실패: {e}[/bold red]")
    if update_fn:
        update_fn()
