import os
import sqlite3
from tkinter import messagebox
from compare import DB_FILE
from logger import logger

def get_cache_counts(method=None, hash_size=None):
    """캐시 건수 조회 (해시, 비교, 진행 상태, 중복 결과)"""
    if not os.path.exists(DB_FILE):
        return 0, 0, 0, 0
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        if method is None or hash_size is None:
            cur.execute("SELECT COUNT(*) FROM hash_cache")
            h_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM compare_cache")
            c_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM compare_progress")
            p_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM duplicate_results")
            d_cnt = cur.fetchone()[0]
        else:
            cur.execute(
                "SELECT COUNT(*) FROM hash_cache WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            h_cnt = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM compare_cache WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            c_cnt = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM compare_progress WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            p_cnt = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM duplicate_results WHERE method=? AND hash_size=?",
                (method, hash_size)
            )
            d_cnt = cur.fetchone()[0]
        conn.close()
        return h_cnt, c_cnt, p_cnt, d_cnt
    except Exception:
        return 0, 0, 0, 0

def clear_cache(update_fn=None):
    """캐시 데이터 초기화"""
    if not messagebox.askyesno("캐시 초기화 확인", "정말로 캐시 데이터를 초기화하시겠습니까?", default=messagebox.NO):
        return

    if os.path.exists(DB_FILE):
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("DELETE FROM hash_cache")
            cur.execute("DELETE FROM compare_cache")
            cur.execute("DELETE FROM compare_progress")
            cur.execute("DELETE FROM duplicate_results")
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