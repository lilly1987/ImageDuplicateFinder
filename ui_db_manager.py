"""
DB 관리 창 모듈.

- 탭 1: 중복 결과 테이블 관리 (목록/내보내기/삭제)
- 탭 2: 해시 캐시 관리 (통계/없는 파일 정리/전체 초기화)
- 탭 3: DB 정보 (파일 크기/백업/복원)
"""

import os
import json
import shutil
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

from database import db_lock, _table_name, _ensure_tables_exist, DB_FILE
from ui_cache import _get_all_table_names, get_cache_counts, clear_cache, drop_db
from logger import logger


_DB_CACHE_BASES = ("hash_cache", "compare_cache", "compare_progress", "duplicate_results")


def _db_size_mb():
    """DB 파일 크기 (MB) 반환"""
    if not os.path.exists(DB_FILE):
        return 0.0
    return os.path.getsize(DB_FILE) / (1024 * 1024)


def _parse_table_meta(table_name, base_name):
    """테이블명에서 (method, hash_size) 추출 (예: hash_cache_ahash_8 → ("ahash", 8))"""
    prefix = base_name + "_"
    if not table_name.startswith(prefix):
        return None
    rest = table_name[len(prefix):]
    parts = rest.rsplit("_", 1)
    if len(parts) != 2:
        return None
    method, hash_size_str = parts
    try:
        return method, int(hash_size_str)
    except ValueError:
        return None


def _format_size(num_bytes):
    """바이트 → 사람이 읽기 쉬운 크기 문자열 (KB/MB/GB)"""
    try:
        num_bytes = int(num_bytes or 0)
    except (ValueError, TypeError):
        return "0 B"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.2f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"


def _estimate_table_data_bytes(cur, table, base):
    """테이블의 순수 데이터 크기(바이트) 대략 계산 (dbstat 사용 불가 시)"""
    try:
        if base == "hash_cache":
            # path + hash 문자열 + mtime(8) + size(8) + 행 오버헤드(24)
            cur.execute(f"SELECT COALESCE(SUM(length(path) + length(hash) + 40), 0) FROM {table}")
        elif base == "compare_cache":
            # file1 + file2 + distance(8) + 행 오버헤드(24)
            cur.execute(f"SELECT COALESCE(SUM(length(file1) + length(file2) + 32), 0) FROM {table}")
        elif base == "compare_progress":
            # path + status(8) + 행 오버헤드(24)
            cur.execute(f"SELECT COALESCE(SUM(length(path) + 32), 0) FROM {table}")
        elif base == "duplicate_results":
            # group_id(8) + path + 행 오버헤드(24)
            cur.execute(f"SELECT COALESCE(SUM(length(path) + 32), 0) FROM {table}")
        else:
            return 0
        return cur.fetchone()[0] or 0
    except Exception:
        return 0


def _collect_table_stats():
    """
    모든 DB 테이블 통계 수집.
    반환: {
        "tables": [
            {
                "name": str, "base": str, "method": str, "hash_size": int,
                "count": int, "groups": int, "size_bytes": int
            }, ...
        ],
        "db_size": float (MB)
    }
    """
    stats = {
        "tables": [],
        "db_size": _db_size_mb(),
    }
    if not os.path.exists(DB_FILE):
        return stats

    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=30)
            try:
                cur = conn.cursor()
                # 전체 DB 파일 크기 (페이지 단위 실제 사용 공간 기반)
                page_size = 4096
                total_pages = 0
                try:
                    cur.execute("PRAGMA page_size")
                    page_size = cur.fetchone()[0] or 4096
                    cur.execute("PRAGMA page_count")
                    total_pages = cur.fetchone()[0] or 0
                except Exception:
                    pass
                total_db_bytes = total_pages * page_size

                for base in _DB_CACHE_BASES:
                    tables = _get_all_table_names(cur, base)
                    for table in tables:
                        meta = _parse_table_meta(table, base)
                        method = meta[0] if meta else "?"
                        hash_size = meta[1] if meta else 0
                        if base == "duplicate_results":
                            # 그룹 수 + 항목 수
                            try:
                                cur.execute(f"SELECT COUNT(DISTINCT group_id) FROM {table}")
                                groups = cur.fetchone()[0] or 0
                            except Exception:
                                groups = 0
                            try:
                                cur.execute(f"SELECT COUNT(*) FROM {table}")
                                count = cur.fetchone()[0] or 0
                            except Exception:
                                count = 0
                        else:
                            groups = 0
                            try:
                                cur.execute(f"SELECT COUNT(*) FROM {table}")
                                count = cur.fetchone()[0] or 0
                            except Exception:
                                count = 0
                        data_bytes = _estimate_table_data_bytes(cur, table, base)
                        stats["tables"].append({
                            "name": table,
                            "base": base,
                            "method": method,
                            "hash_size": hash_size,
                            "count": count,
                            "groups": groups,
                            "size_bytes": data_bytes,
                        })
            finally:
                conn.close()

        # 전체 DB 파일 크기 대비 비율로 페이지 오버헤드 배분 (근사 조정)
        total_data_bytes = sum(t["size_bytes"] for t in stats["tables"])
        if total_data_bytes > 0 and total_db_bytes > 0:
            scale = total_db_bytes / total_data_bytes
            if scale > 100:  # 비정상적으로 큰 비율은 제한 (freelist 등 제외)
                scale = min(scale, 3.0)
            for t in stats["tables"]:
                t["size_bytes"] = int(t["size_bytes"] * scale)
    except Exception as e:
        logger.error(f"[DB 관리] 테이블 통계 수집 오류: {e}")

    return stats


def _export_duplicate_table_to_json(table_name):
    """중복 결과 테이블을 UI 결과창 JSON 형식으로 내보내기"""
    meta = _parse_table_meta(table_name, "duplicate_results")
    if not meta:
        return None
    method, hash_size = meta

    from comparator import load_duplicate_results_from_db
    groups = load_duplicate_results_from_db(method, hash_size)
    if not groups:
        return None

    # tolerance는 DB에 저장되어 있지 않으므로 config 기본값 사용
    from results import resolve_search_options
    _m, _h, aspect_ratio_tol, tolerance = resolve_search_options()
    if aspect_ratio_tol is None:
        aspect_ratio_tol = 0.01
    if tolerance is None:
        tolerance = 1

    data = {
        "saved_at": datetime.now().isoformat(),
        "search_options": {
            "method": method,
            "hash_size": hash_size,
            "aspect_ratio_tol": aspect_ratio_tol,
            "tolerance": tolerance,
        },
        "groups": groups,
    }
    return data


def _remove_missing_hash_files(progress_callback=None):
    """
    모든 hash_cache 테이블에서 존재하지 않는 파일의 해시 제거.
    - hash_cache: 해당 파일 해시 제거
    - compare_cache: 해당 파일이 포함된 비교 쌍 제거
    - compare_progress: 해당 파일 진행 상태 제거
    - duplicate_results: 해당 파일이 포함된 그룹에서 제거
    """
    if not os.path.exists(DB_FILE):
        return 0

    total_removed = 0
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=60)
            try:
                cur = conn.cursor()
                for base in _DB_CACHE_BASES:
                    tables = _get_all_table_names(cur, base)
                    for table in tables:
                        # hash_cache에서만 파일 존재 여부 확인 (중복 방지)
                        if base != "hash_cache":
                            continue
                        meta = _parse_table_meta(table, base)
                        if not meta:
                            continue
                        method, hash_size = meta
                        # 해당 (method, hash_size)의 모든 테이블명
                        hash_t = table
                        compare_t = _table_name("compare_cache", method, hash_size)
                        progress_t = _table_name("compare_progress", method, hash_size)
                        results_t = _table_name("duplicate_results", method, hash_size)

                        cur.execute(f"SELECT path FROM {hash_t}")
                        rows = cur.fetchall()
                        missing = []
                        for (path,) in rows:
                            if not os.path.exists(path):
                                missing.append(path)

                        if missing:
                            total_removed += len(missing)
                            for path in missing:
                                cur.execute(f"DELETE FROM {hash_t} WHERE path=?", (path,))
                                cur.execute(f"DELETE FROM {compare_t} WHERE file1=? OR file2=?", (path, path))
                                cur.execute(f"DELETE FROM {progress_t} WHERE path=?", (path,))
                                cur.execute(f"DELETE FROM {results_t} WHERE path=?", (path,))
                            if progress_callback:
                                progress_callback(f"[{method}, h{hash_size}] 존재하지 않는 파일 {len(missing)}개 제거")
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.error(f"[DB 관리] 없는 파일 해시 정리 오류: {e}")
        raise

    return total_removed


def _backup_db():
    """DB 파일 백업 (WAL/SHM 포함)"""
    if not os.path.exists(DB_FILE):
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"cache_backup_{timestamp}.db"
    # WAL/SHM 파일도 함께 복사 (데이터 일관성 유지)
    shutil.copy2(DB_FILE, backup_path)
    for suffix in ("-wal", "-shm"):
        wal_path = DB_FILE + suffix
        if os.path.exists(wal_path):
            try:
                shutil.copy2(wal_path, backup_path + suffix)
            except Exception:
                pass
    return backup_path


def _restore_db(backup_path):
    """백업 파일에서 DB 복원"""
    if not os.path.exists(backup_path):
        return False
    # 현재 DB 연결이 사용 중일 수 있으므로 WAL 체크포인트 후 복원
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, timeout=30)
            try:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
            finally:
                conn.close()
    except Exception:
        pass

    # 기존 DB + WAL/SHM 파일 삭제 후 복원
    for suffix in ("", "-wal", "-shm"):
        target = DB_FILE + suffix
        if os.path.exists(target):
            try:
                os.remove(target)
            except Exception:
                pass
    shutil.copy2(backup_path, DB_FILE)
    for suffix in ("-wal", "-shm"):
        src = backup_path + suffix
        if os.path.exists(src):
            try:
                shutil.copy2(src, DB_FILE + suffix)
            except Exception:
                pass
    return True


# ============================================================
# DB 관리 창
# ============================================================
def show_db_manager_window(root, lang):
    win = tk.Toplevel(root)
    win.title(lang["ui"].get("db_manager_title", "DB 관리"))
    win.geometry("820x560")

    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=5, pady=5)

    # ==========================================================
    # 탭 1: 중복 결과 테이블 관리
    # ==========================================================
    tab_results = tk.Frame(notebook)
    notebook.add(tab_results, text=lang["ui"].get("db_tab_results", "중복 결과 테이블"))

    # 상단 툴바
    result_toolbar = tk.Frame(tab_results)
    result_toolbar.pack(side="top", fill="x", padx=3, pady=3)

    refresh_btn = tk.Button(
        result_toolbar,
        text=lang["ui"].get("db_refresh", "새로고침"),
        command=lambda: _refresh_result_table_list(),
    )
    refresh_btn.pack(side="left", padx=(0, 3))

    export_btn = tk.Button(
        result_toolbar,
        text=lang["ui"].get("db_export_json", "JSON 내보내기"),
        command=lambda: _export_selected_table(),
    )
    export_btn.pack(side="left", padx=(0, 3))

    delete_btn = tk.Button(
        result_toolbar,
        text=lang["ui"].get("db_delete_table", "테이블 삭제"),
        command=lambda: _delete_selected_table(),
    )
    delete_btn.pack(side="left", padx=(0, 3))

    open_results_btn = tk.Button(
        result_toolbar,
        text=lang["ui"].get("db_open_results", "결과창에서 열기"),
        command=lambda: _open_selected_in_results(),
    )
    open_results_btn.pack(side="left", padx=(0, 3))

    # 트리 뷰 (테이블 목록)
    result_tree_frame = tk.Frame(tab_results)
    result_tree_frame.pack(fill="both", expand=True, padx=3, pady=3)

    result_tree = ttk.Treeview(
        result_tree_frame,
        columns=("base", "method", "hash_size", "count", "groups", "size"),
        show="headings",
    )
    result_tree.heading("base", text=lang["ui"].get("db_col_base", "유형"))
    result_tree.heading("method", text=lang["ui"].get("db_col_method", "알고리즘"))
    result_tree.heading("hash_size", text=lang["ui"].get("db_col_hash_size", "해시 크기"))
    result_tree.heading("count", text=lang["ui"].get("db_col_count", "건수"))
    result_tree.heading("groups", text=lang["ui"].get("db_col_groups", "그룹 수"))
    result_tree.heading("size", text=lang["ui"].get("db_col_size", "크기"))
    result_tree.column("base", width=120, anchor="w")
    result_tree.column("method", width=90, anchor="w")
    result_tree.column("hash_size", width=70, anchor="center")
    result_tree.column("count", width=90, anchor="e")
    result_tree.column("groups", width=70, anchor="e")
    result_tree.column("size", width=90, anchor="e")
    result_tree.pack(side="left", fill="both", expand=True)

    result_scroll = ttk.Scrollbar(result_tree_frame, orient="vertical", command=result_tree.yview)
    result_tree.configure(yscrollcommand=result_scroll.set)
    result_scroll.pack(side="right", fill="y")

    result_info_label = tk.Label(tab_results, text="", anchor="w", fg="gray30")
    result_info_label.pack(side="bottom", fill="x", padx=5, pady=3)

    _result_table_data = []  # [(tree_iid, {"name":..., "base":..., "method":..., "hash_size":...})]

    def _refresh_result_table_list():
        """테이블 목록 새로고침"""
        nonlocal _result_table_data
        for item in result_tree.get_children():
            result_tree.delete(item)
        _result_table_data.clear()

        stats = _collect_table_stats()
        for t in stats["tables"]:
            if t["base"] != "duplicate_results":
                continue
            base_label = {
                "duplicate_results": lang["ui"].get("db_base_results", "중복 결과"),
            }.get(t["base"], t["base"])
            iid = result_tree.insert(
                "", "end",
                values=(
                    base_label,
                    t["method"],
                    t["hash_size"],
                    f"{t['count']:,}",
                    f"{t['groups']:,}",
                    _format_size(t["size_bytes"]),
                ),
            )
            _result_table_data.append({
                "iid": iid,
                "name": t["name"],
                "base": t["base"],
                "method": t["method"],
                "hash_size": t["hash_size"],
            })
        result_info_label.config(text=f"{lang['ui'].get('db_table_count', '테이블 수')}: {len(_result_table_data)}")

    def _get_selected_table():
        """선택된 테이블 정보 반환"""
        selected = result_tree.selection()
        if not selected:
            return None
        iid = selected[0]
        for item in _result_table_data:
            if item["iid"] == iid:
                return item
        return None

    def _get_selected_tables():
        """선택된 테이블 정보 목록 반환 (다중 선택 지원)"""
        selected = result_tree.selection()
        if not selected:
            return []
        selected_set = set(selected)
        return [item for item in _result_table_data if item["iid"] in selected_set]

    def _export_selected_table():
        """선택된 중복 결과 테이블을 JSON으로 내보내기 (다중 선택 지원)"""
        items = _get_selected_tables()
        if not items:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_select_table_first", "테이블을 선택하세요."),
                parent=win,
            )
            return

        # 첫 번째 항목은 저장 대화상자, 나머지는 같은 폴더에 자동 이름으로 저장
        first = items[0]
        data = _export_duplicate_table_to_json(first["name"])
        if data is None:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_no_data_to_export", "내보낼 데이터가 없습니다."),
                parent=win,
            )
            return
        default_name = f"duplicate_results_{first['method']}_h{first['hash_size']}_export.json"
        path = filedialog.asksaveasfilename(
            parent=win,
            title=lang["ui"].get("db_export_json", "JSON 내보내기"),
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON files", "*.json")],
        )
        if not path:
            return
        saved_dir = os.path.dirname(path)

        exported_count = 0
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            exported_count += 1
        except Exception as e:
            messagebox.showerror(
                lang["ui"].get("info", "정보"),
                f"{lang['ui'].get('db_export_error', '내보내기 실패')}: {e}",
                parent=win,
            )
            return

        # 나머지 선택 항목은 같은 폴더에 자동 이름으로 저장
        for item in items[1:]:
            data = _export_duplicate_table_to_json(item["name"])
            if data is None:
                continue
            auto_name = f"duplicate_results_{item['method']}_h{item['hash_size']}_export.json"
            auto_path = os.path.join(saved_dir, auto_name)
            try:
                with open(auto_path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                exported_count += 1
            except Exception as e:
                messagebox.showerror(
                    lang["ui"].get("info", "정보"),
                    f"{lang['ui'].get('db_export_error', '내보내기 실패')}: {e}",
                    parent=win,
                )

        messagebox.showinfo(
            lang["ui"].get("info", "정보"),
            lang["ui"].get("db_export_success", "내보내기가 완료되었습니다.") + f" ({exported_count}개)",
            parent=win,
        )
        logger.info(f"[bold cyan][알림] DB 결과 JSON 내보내기 완료: {exported_count}개 → {saved_dir}[/bold cyan]")

    def _delete_selected_table():
        """선택된 중복 결과 테이블 일괄 삭제 (다중 선택 지원)"""
        items = _get_selected_tables()
        if not items:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_select_table_first", "테이블을 선택하세요."),
                parent=win,
            )
            return
        confirm = messagebox.askyesno(
            lang["ui"].get("confirm", "확인"),
            f"{lang['ui'].get('db_delete_table_confirm', '테이블을 삭제하시겠습니까?')}\n" + "\n".join(it["name"] for it in items),
            parent=win,
            default=messagebox.NO,
        )
        if not confirm:
            return
        try:
            with db_lock:
                conn = sqlite3.connect(DB_FILE, timeout=30)
                try:
                    cur = conn.cursor()
                    for it in items:
                        cur.execute(f"DROP TABLE IF EXISTS {it['name']}")
                    conn.commit()
                finally:
                    conn.close()
            _refresh_result_table_list()
            logger.info(f"[bold cyan][알림] DB 테이블 삭제 완료: {len(items)}개[/bold cyan]")
        except Exception as e:
            messagebox.showerror(
                lang["ui"].get("info", "정보"),
                f"{lang['ui'].get('db_delete_error', '테이블 삭제 실패')}: {e}",
                parent=win,
            )

    def _open_selected_in_results():
        """선택된 중복 결과 테이블을 결과창에서 열기 (드롭다운 선택 방식 재사용)"""
        item = _get_selected_table()
        if item is None:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_select_table_first", "테이블을 선택하세요."),
                parent=win,
            )
            return
        try:
            from run import show_single_window
            from ui_results import show_duplicate_results_window
            # 결과창을 열고, 해당 테이블을 드롭다운에서 선택하도록 함
            results_win = show_single_window("results", show_duplicate_results_window, root, lang)
            if results_win is not None:
                # 드롭다운에서 해당 테이블 항목 선택
                def _select_in_combo():
                    try:
                        combo = results_win._search_combo
                        valid_options = getattr(combo, "_valid_options", [])
                        target_label = f"{item['method']} / hash_size={item['hash_size']}"
                        for idx, opt in enumerate(valid_options):
                            if opt[0] == target_label:
                                combo.current(idx)
                                combo.event_generate("<<ComboboxSelected>>")
                                break
                    except Exception:
                        pass
                results_win.after(300, _select_in_combo)
        except Exception as e:
            logger.error(f"[DB 관리] 결과창 열기 오류: {e}")

    _refresh_result_table_list()

    # ==========================================================
    # 탭 2: 해시 캐시 관리
    # ==========================================================
    tab_cache = tk.Frame(notebook)
    notebook.add(tab_cache, text=lang["ui"].get("db_tab_hash_cache", "해시 캐시"))

    cache_toolbar = tk.Frame(tab_cache)
    cache_toolbar.pack(side="top", fill="x", padx=3, pady=3)

    cache_refresh_btn = tk.Button(
        cache_toolbar,
        text=lang["ui"].get("db_refresh", "새로고침"),
        command=lambda: _refresh_cache_stats(),
    )
    cache_refresh_btn.pack(side="left", padx=(0, 3))

    cleanup_btn = tk.Button(
        cache_toolbar,
        text=lang["ui"].get("db_cleanup_missing", "없는 파일 정리"),
        command=lambda: _cleanup_missing_files(),
    )
    cleanup_btn.pack(side="left", padx=(0, 3))

    delete_selected_btn = tk.Button(
        cache_toolbar,
        text=lang["ui"].get("db_delete_selected_hash", "선택한 알고리즘/길이 삭제"),
        command=lambda: _delete_selected_hash_cache(),
    )
    delete_selected_btn.pack(side="left", padx=(0, 3))

    clear_all_btn = tk.Button(
        cache_toolbar,
        text=lang["ui"].get("db_clear_cache", "전체 캐시 초기화"),
        command=lambda: clear_cache(_refresh_cache_stats),
    )
    clear_all_btn.pack(side="left", padx=(0, 3))

    # 캐시 통계 라벨
    cache_stats_label = tk.Label(cache_toolbar, text="", fg="gray30")
    cache_stats_label.pack(side="left", padx=(10, 0))

    # 캐시 테이블 트리
    cache_tree_frame = tk.Frame(tab_cache)
    cache_tree_frame.pack(fill="both", expand=True, padx=3, pady=3)

    cache_tree = ttk.Treeview(
        cache_tree_frame,
        columns=("method", "hash_size", "hash_count", "compare_count", "progress_count", "size"),
        show="headings",
    )
    cache_tree.heading("method", text=lang["ui"].get("db_col_method", "알고리즘"))
    cache_tree.heading("hash_size", text=lang["ui"].get("db_col_hash_size", "해시 크기"))
    cache_tree.heading("hash_count", text=lang["ui"].get("db_col_hash_count", "해시"))
    cache_tree.heading("compare_count", text=lang["ui"].get("db_col_compare_count", "비교"))
    cache_tree.heading("progress_count", text=lang["ui"].get("db_col_progress_count", "진행"))
    cache_tree.heading("size", text=lang["ui"].get("db_col_size", "크기"))
    cache_tree.column("method", width=100, anchor="w")
    cache_tree.column("hash_size", width=80, anchor="center")
    cache_tree.column("hash_count", width=100, anchor="e")
    cache_tree.column("compare_count", width=100, anchor="e")
    cache_tree.column("progress_count", width=100, anchor="e")
    cache_tree.column("size", width=80, anchor="e")
    cache_tree.pack(side="left", fill="both", expand=True)

    cache_scroll = ttk.Scrollbar(cache_tree_frame, orient="vertical", command=cache_tree.yview)
    cache_tree.configure(yscrollcommand=cache_scroll.set)
    cache_scroll.pack(side="right", fill="y")

    def _refresh_cache_stats():
        """해시 캐시 통계 새로고침"""
        for item in cache_tree.get_children():
            cache_tree.delete(item)

        stats = _collect_table_stats()
        # (method, hash_size)별 집계 (용량 포함)
        agg = {}
        for t in stats["tables"]:
            key = (t["method"], t["hash_size"])
            entry = agg.setdefault(key, {"hash": 0, "compare": 0, "progress": 0, "size": 0})
            if t["base"] == "hash_cache":
                entry["hash"] = t["count"]
            elif t["base"] == "compare_cache":
                entry["compare"] = t["count"]
            elif t["base"] == "compare_progress":
                entry["progress"] = t["count"]
            entry["size"] += t["size_bytes"]

        for (method, hash_size), entry in sorted(agg.items()):
            cache_tree.insert(
                "", "end",
                values=(
                    method,
                    hash_size,
                    f"{entry['hash']:,}",
                    f"{entry['compare']:,}",
                    f"{entry['progress']:,}",
                    _format_size(entry["size"]),
                ),
            )

        h_cnt, c_cnt, p_cnt, d_cnt = get_cache_counts()
        cache_stats_label.config(
            text=lang["ui"].get(
                "db_cache_summary",
                "해시 {h:,} / 비교 {c:,} / 진행 {p:,} / 중복 {d:,}",
            ).format(h=h_cnt, c=c_cnt, p=p_cnt, d=d_cnt)
        )

    def _get_selected_hash_cache_list():
        """선택된 (method, hash_size) 목록 반환 (다중 선택 지원)"""
        selected = cache_tree.selection()
        result = []
        for iid in selected:
            vals = cache_tree.item(iid, "values")
            if len(vals) < 2:
                continue
            try:
                hash_size = int(vals[1])
            except (ValueError, TypeError):
                continue
            result.append((str(vals[0]), hash_size))
        return result

    def _delete_selected_hash_cache():
        """선택한 알고리즘/길이의 해시 캐시 테이블 일괄 삭제 (다중 선택 지원)"""
        metas = _get_selected_hash_cache_list()
        if not metas:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_select_cache_first", "삭제할 알고리즘/길이를 선택하세요."),
                parent=win,
            )
            return

        # 확인 대화상자에 선택된 모든 항목 표시
        target_lines = "\n".join(f"[{m}, h{s}]" for m, s in metas)
        confirm = messagebox.askyesno(
            lang["ui"].get("confirm", "확인"),
            lang["ui"].get("db_delete_selected_hash_confirm", "선택한 알고리즘/길이의 캐시를 삭제하시겠습니까?").format(
                method=metas[0][0], hash_size=metas[0][1]
            )
            + "\n" + target_lines + "\n"
            + lang["ui"].get("db_delete_selected_hash_tables", "해시/비교/진행/중복 결과 테이블이 모두 삭제됩니다."),
            parent=win,
            default=messagebox.NO,
        )
        if not confirm:
            return

        deleted_count = 0
        try:
            with db_lock:
                conn = sqlite3.connect(DB_FILE, timeout=30)
                try:
                    cur = conn.cursor()
                    for method, hash_size in metas:
                        hash_t = _table_name("hash_cache", method, hash_size)
                        compare_t = _table_name("compare_cache", method, hash_size)
                        progress_t = _table_name("compare_progress", method, hash_size)
                        results_t = _table_name("duplicate_results", method, hash_size)
                        for table in (hash_t, compare_t, progress_t, results_t):
                            cur.execute(f"DROP TABLE IF EXISTS {table}")
                        deleted_count += 1
                    conn.commit()
                finally:
                    conn.close()
            _refresh_cache_stats()
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_delete_selected_hash_done", "삭제가 완료되었습니다.") + f" ({deleted_count}개)",
                parent=win,
            )
            logger.info(f"[bold cyan][알림] DB 해시 캐시 삭제 완료: {deleted_count}개 알고리즘/길이[/bold cyan]")
        except Exception as e:
            messagebox.showerror(
                lang["ui"].get("info", "정보"),
                f"{lang['ui'].get('db_delete_error', '삭제 실패')}: {e}",
                parent=win,
            )

    def _cleanup_missing_files():
        """존재하지 않는 파일의 해시/비교/진행/중복 데이터 일괄 정리"""
        confirm = messagebox.askyesno(
            lang["ui"].get("confirm", "확인"),
            lang["ui"].get("db_cleanup_confirm", "존재하지 않는 파일의 캐시 데이터를 모두 정리하시겠습니까?"),
            parent=win,
            default=messagebox.NO,
        )
        if not confirm:
            return

        # 백그라운드 스레드에서 실행 (파일 시스템 검사는 오래 걸릴 수 있음)
        def worker():
            try:
                def progress_cb(msg):
                    win.after(0, lambda: cache_stats_label.config(text=msg))

                removed = _remove_missing_hash_files(progress_cb)
                win.after(0, lambda: _refresh_cache_stats())
                win.after(
                    0,
                    lambda: messagebox.showinfo(
                        lang["ui"].get("info", "정보"),
                        lang["ui"].get("db_cleanup_done", "정리가 완료되었습니다.") + f" ({removed:,}개 제거)",
                        parent=win,
                    ),
                )
                logger.info(f"[bold cyan][알림] DB 없는 파일 정리 완료: {removed:,}개 제거[/bold cyan]")
            except Exception as e:
                win.after(
                    0,
                    lambda: messagebox.showerror(
                        lang["ui"].get("info", "정보"),
                        f"{lang['ui'].get('db_cleanup_error', '정리 실패')}: {e}",
                        parent=win,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    _refresh_cache_stats()

    # ==========================================================
    # 탭 3: DB 정보 / 백업 / 복원
    # ==========================================================
    tab_info = tk.Frame(notebook)
    notebook.add(tab_info, text=lang["ui"].get("db_tab_info", "DB 정보/백업"))

    info_frame = tk.Frame(tab_info)
    info_frame.pack(fill="x", padx=10, pady=10)

    db_size_label = tk.Label(info_frame, text="", anchor="w", font=("", 10, "bold"))
    db_size_label.grid(row=0, column=0, sticky="w")

    db_path_label = tk.Label(info_frame, text=f"  {os.path.abspath(DB_FILE)}", anchor="w", fg="gray")
    db_path_label.grid(row=1, column=0, sticky="w")

    info_detail_label = tk.Label(info_frame, text="", anchor="w", fg="gray30")
    info_detail_label.grid(row=2, column=0, sticky="w")

    # 백업/복원 버튼 프레임
    backup_frame = tk.Frame(tab_info)
    backup_frame.pack(fill="x", padx=10, pady=5)

    backup_btn = tk.Button(
        backup_frame,
        text=lang["ui"].get("db_backup", "백업"),
        command=lambda: _do_backup(),
    )
    backup_btn.pack(side="left", padx=(0, 5))

    restore_btn = tk.Button(
        backup_frame,
        text=lang["ui"].get("db_restore", "복원"),
        command=lambda: _do_restore(),
    )
    restore_btn.pack(side="left", padx=(0, 5))

    delete_db_btn = tk.Button(
        backup_frame,
        text=lang["ui"].get("db_drop_db", "DB 파일 삭제"),
        command=lambda: drop_db(_refresh_info),
    )
    delete_db_btn.pack(side="left", padx=(0, 5))

    backup_desc = tk.Label(
        tab_info,
        text=lang["ui"].get("db_backup_desc", "백업 파일은 cache_backup_YYYYMMDD_HHMMSS.db 형식으로 저장됩니다."),
        anchor="w",
        fg="gray",
        justify="left",
        wraplength=700,
    )
    backup_desc.pack(fill="x", padx=10, pady=(0, 5))

    # 백업 파일 목록 트리
    backup_list_label = tk.Label(
        tab_info,
        text=lang["ui"].get("db_backup_list", "백업 파일 목록"),
        anchor="w",
    )
    backup_list_label.pack(fill="x", padx=10, pady=(5, 0))

    backup_tree_frame = tk.Frame(tab_info)
    backup_tree_frame.pack(fill="both", expand=True, padx=10, pady=3)

    backup_tree = ttk.Treeview(
        backup_tree_frame,
        columns=("name", "size"),
        show="headings",
    )
    backup_tree.heading("name", text=lang["ui"].get("db_col_backup_name", "파일명"))
    backup_tree.heading("size", text=lang["ui"].get("db_col_size", "크기"))
    backup_tree.column("name", width=380, anchor="w")
    backup_tree.column("size", width=100, anchor="e")
    backup_tree.pack(side="left", fill="both", expand=True)

    backup_scroll = ttk.Scrollbar(backup_tree_frame, orient="vertical", command=backup_tree.yview)
    backup_tree.configure(yscrollcommand=backup_scroll.set)
    backup_scroll.pack(side="right", fill="y")
    backup_tree.bind("<Double-1>", lambda e: _do_restore_from_selected())

    # 백업 파일 선택 시 복원 버튼
    restore_selected_btn = tk.Button(
        tab_info,
        text=lang["ui"].get("db_restore_selected", "선택한 백업 파일로 복원"),
        command=lambda: _do_restore_from_selected(),
    )
    restore_selected_btn.pack(pady=3)

    def _refresh_info():
        """DB 정보 새로고침"""
        stats = _collect_table_stats()
        size_mb = stats["db_size"]
        db_size_label.config(
            text=lang["ui"].get("db_size_label", "DB 크기: {size:.2f} MB").format(size=size_mb)
        )
        table_count = len(stats["tables"])
        h_cnt, c_cnt, p_cnt, d_cnt = get_cache_counts()
        info_detail_label.config(
            text=lang["ui"].get(
                "db_info_detail",
                "테이블 {t}개 | 해시 {h:,} / 비교 {c:,} / 진행 {p:,} / 중복 {d:,}",
            ).format(t=table_count, h=h_cnt, c=c_cnt, p=p_cnt, d=d_cnt)
        )
        # 백업 파일 목록 갱신
        for item in backup_tree.get_children():
            backup_tree.delete(item)
        backup_files = sorted(
            [f for f in os.listdir(".") if f.startswith("cache_backup_") and f.endswith(".db")],
            reverse=True,
        )
        for name in backup_files:
            try:
                size = os.path.getsize(name) / 1024
                backup_tree.insert("", "end", values=(name, f"{size:.1f} KB"))
            except Exception:
                backup_tree.insert("", "end", values=(name, "?"))

    def _do_backup():
        """DB 백업 실행"""
        path = _backup_db()
        if path is None:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_no_db", "DB 파일이 없습니다."),
                parent=win,
            )
            return
        _refresh_info()
        messagebox.showinfo(
            lang["ui"].get("info", "정보"),
            lang["ui"].get("db_backup_done", "백업이 완료되었습니다.") + f"\n{path}",
            parent=win,
        )
        logger.info(f"[bold cyan][알림] DB 백업 완료: {path}[/bold cyan]")

    def _do_restore():
        """백업 파일 선택 후 복원"""
        path = filedialog.askopenfilename(
            parent=win,
            title=lang["ui"].get("db_restore", "복원"),
            defaultextension=".db",
            filetypes=[("DB files", "*.db")],
        )
        if not path:
            return
        confirm = messagebox.askyesno(
            lang["ui"].get("confirm", "확인"),
            lang["ui"].get("db_restore_confirm", "현재 DB를 백업 파일로 대체하시겠습니까?\n현재 DB는 삭제됩니다."),
            parent=win,
            default=messagebox.NO,
        )
        if not confirm:
            return
        try:
            ok = _restore_db(path)
            if ok:
                _refresh_info()
                messagebox.showinfo(
                    lang["ui"].get("info", "정보"),
                    lang["ui"].get("db_restore_done", "복원이 완료되었습니다."),
                    parent=win,
                )
                logger.info(f"[bold cyan][알림] DB 복원 완료: {path}[/bold cyan]")
            else:
                messagebox.showerror(
                    lang["ui"].get("info", "정보"),
                    lang["ui"].get("db_restore_error", "복원할 백업 파일이 없습니다."),
                    parent=win,
                )
        except Exception as e:
            messagebox.showerror(
                lang["ui"].get("info", "정보"),
                f"{lang['ui'].get('db_restore_error', '복원 실패')}: {e}",
                parent=win,
            )

    def _do_restore_from_selected():
        """백업 목록에서 선택한 파일로 복원"""
        selected = backup_tree.selection()
        if not selected:
            messagebox.showinfo(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("db_select_backup_first", "백업 파일을 선택하세요."),
                parent=win,
            )
            return
        name = backup_tree.item(selected[0], "values")[0]
        path = os.path.join(".", name)
        confirm = messagebox.askyesno(
            lang["ui"].get("confirm", "확인"),
            lang["ui"].get("db_restore_confirm", "현재 DB를 백업 파일로 대체하시겠습니까?\n현재 DB는 삭제됩니다."),
            parent=win,
            default=messagebox.NO,
        )
        if not confirm:
            return
        try:
            ok = _restore_db(path)
            if ok:
                _refresh_info()
                messagebox.showinfo(
                    lang["ui"].get("info", "정보"),
                    lang["ui"].get("db_restore_done", "복원이 완료되었습니다."),
                    parent=win,
                )
                logger.info(f"[bold cyan][알림] DB 복원 완료: {path}[/bold cyan]")
        except Exception as e:
            messagebox.showerror(
                lang["ui"].get("info", "정보"),
                f"{lang['ui'].get('db_restore_error', '복원 실패')}: {e}",
                parent=win,
            )

    _refresh_info()

    return win