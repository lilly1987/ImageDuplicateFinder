import os
import sqlite3
from tkinter import messagebox
from compare import DB_FILE
from logger import logger

def get_cache_counts(method=None, hash_size=None):
    if not os.path.exists(DB_FILE):
        return 0, 0
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        if method is None or hash_size is None:
            cur.execute("SELECT COUNT(*) FROM hash_cache")
            h_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM compare_cache")
            c_cnt = cur.fetchone()[0]
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
        conn.close()
        return h_cnt, c_cnt
    except Exception:
        return 0, 0

def clear_cache(update_fn=None):
    if not messagebox.askyesno("캐시 초기화 확인", "정말로 캐시 데이터를 초기화하시겠습니까?", default=messagebox.NO):
        return

    if os.path.exists(DB_FILE):
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("DELETE FROM hash_cache")
            cur.execute("DELETE FROM compare_cache")
            conn.commit()
            conn.close()
            logger.info("[bold green][알림] 캐시 데이터가 초기화되었습니다.[/bold green]")
        except Exception as e:
            logger.error(f"[bold red][오류] 캐시 초기화 실패: {e}[/bold red]")
    if update_fn:
        update_fn()

def drop_db(update_fn=None):
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
