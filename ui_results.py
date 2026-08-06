"""
중복 결과 창 모듈.

- 결과창은 원본 중복 결과의 복사본으로 작업 (검사 엔진과 격리)
- 실시간 갱신 시 saved_groups 동기화
- 폴더 선택창에서 체크 해제된 폴더의 파일은 결과에서 제외
- 창 닫기 시 삭제/제거 내용을 원본에 반영 (JSON 저장 + DB 캐시 정리)
"""

import os
import json
import copy
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from PIL import Image, ImageTk
from compare import (
    load_duplicate_results_json,
    duplicate_results_json_path,
    resolve_search_options,
    remove_missing_files_from_cache,
    get_duplicate_groups,
    is_stop_requested,
)
from tooltip import add_tooltip
from logger import logger


def show_duplicate_results_window(root, lang, folder_list=None):
    win = tk.Toplevel(root)
    win.title(lang["ui"].get("duplicate_results_window", "중복 결과"))
    win.geometry("1000x600")

    # 상단 컨트롤 바 (검색 결과 선택 드롭다운 + 버튼들)
    control_bar = tk.Frame(win)
    control_bar.pack(side="top", fill="x")

    # 검색 결과 선택 드롭다운
    tk.Label(control_bar, text="검색 결과:").pack(side="left", padx=(5, 2))
    search_var = tk.StringVar()
    search_combo = ttk.Combobox(control_bar, textvariable=search_var, state="readonly", width=50)
    search_combo.pack(side="left", padx=(0, 10))

    # 중복 폴더 유형 필터 드롭다운
    tk.Label(control_bar, text="폴더 필터:").pack(side="left", padx=(5, 2))
    folder_filter_var = tk.StringVar(value="전체 보기")
    folder_filter_combo = ttk.Combobox(
        control_bar,
        textvariable=folder_filter_var,
        values=["전체 보기", "폴더 간 중복만", "폴더 내 중복만"],
        state="readonly",
        width=14
    )
    folder_filter_combo.pack(side="left", padx=(0, 5))
    folder_filter_combo.bind("<<ComboboxSelected>>", lambda e: _apply_path_filter())

    # 파일 경로 필터 입력
    tk.Label(control_bar, text="경로 필터:").pack(side="left", padx=(5, 2))
    path_filter_var = tk.StringVar()
    path_filter_entry = tk.Entry(control_bar, textvariable=path_filter_var, width=20)
    path_filter_entry.pack(side="left", padx=(0, 5))
    path_filter_entry.bind("<KeyRelease>", lambda e: _apply_path_filter())

    # 라디오 버튼: 전체그룹 / 체크 포함 그룹 / 체크 없는 그룹
    group_view_var = tk.StringVar(value="all")
    rb_all_groups = tk.Radiobutton(control_bar, text="전체그룹", variable=group_view_var, value="all", command=lambda: _apply_path_filter())
    rb_all_groups.pack(side="left", padx=(5, 2))
    rb_checked_groups = tk.Radiobutton(control_bar, text="체크 포함 그룹", variable=group_view_var, value="has_checked", command=lambda: _apply_path_filter())
    rb_checked_groups.pack(side="left", padx=(2, 2))
    rb_unchecked_groups = tk.Radiobutton(control_bar, text="체크 없는 그룹", variable=group_view_var, value="no_checked", command=lambda: _apply_path_filter())
    rb_unchecked_groups.pack(side="left", padx=(2, 2))

    # 자동 재갱신 체크박스 (기본: 해제 상태)
    # - 체크: 실시간 2초 폴링 자동 갱신 + 비교 완료 시 결과 자동 재로드
    # - 해제: 수동 새로고침만 동작
    auto_refresh_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        control_bar,
        text=lang["ui"].get("auto_refresh_results", "자동 재갱신"),
        variable=auto_refresh_var,
        command=lambda: _on_auto_refresh_toggle(),
    ).pack(side="left", padx=(5, 2))

    # 해시 보여주기 체크박스 (기본: 해제 상태)
    # - 체크: 미리보기에서 폴더경로 아래에 해시값 표시
    # - 해제: 해시값 숨김
    show_hash_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        control_bar,
        text=lang["ui"].get("show_hash", "해시 보여주기"),
        variable=show_hash_var,
        command=lambda: _on_show_hash_toggle(),
    ).pack(side="left", padx=(5, 2))

    # ============================================================
    # 폴더 선택창의 체크 상태 반영 필터
    # - 폴더 선택창(folder_list)에서 체크 해제된 폴더의 파일은 결과에서 제외
    # ============================================================
    _folder_filter_paths = None
    _checked_folders_set = None
    _all_folders_checked = False

    if folder_list is not None:
        try:
            from folder_list import get_checked_folders, get_all_folders
            checked = set(get_checked_folders(folder_list))
            all_f = set(get_all_folders(folder_list))
            if len(checked) == len(all_f) and all_f:
                _all_folders_checked = True  # 전체 체크 상태면 필터 무시 (O(1) 통과)
            else:
                _folder_filter_paths = checked
                # 빠른 조회를 위해 표준화된 경로 세트 구축
                _checked_folders_set = {
                    os.path.normpath(f).lower() for f in checked
                }
        except Exception:
            _folder_filter_paths = None

    def _is_in_checked_folder(file_path):
        """파일이 체크된 폴더(또는 그 상위/하위)에 속하는지 빠르게 확인 (부모 경로 상승 방식 - O(깊이))"""
        if _all_folders_checked or not _checked_folders_set:
            return True
        cur = os.path.normpath(file_path).lower()
        # 부모 디렉토리로 올라가며 체크된 폴더 세트에 존재하는지 확인 (경로 깊이만큼만 반복, 최대 5~10회)
        while True:
            parent = os.path.dirname(cur)
            if parent == cur:  # 루트 달성
                break
            if parent in _checked_folders_set:
                return True
            cur = parent
        return False

    def _filter_groups_by_folder(groups):
        """체크 해제된 폴더의 파일을 그룹에서 제외 (그룹이 1개 이하가 되면 그룹 제외)"""
        if _all_folders_checked or not _checked_folders_set or not groups:
            return groups
        result = []
        for group in groups:
            filtered = [p for p in group if _is_in_checked_folder(p)]
            if len(filtered) > 1:
                result.append(filtered)
        return result

    def refresh_search_list():
        """DB 테이블 + JSON 파일 + 진행 중(Live) 검색 결과 목록 조회 (건수 계산 없음)"""
        import glob
        options = []
        seen = set()

        # 0. 진행 중이면 "실시간 (진행 중)" 항목 최상단 추가
        if not is_stop_requested():
            try:
                options.append(("실시간 (진행 중)", "live", None, None, None, None))
            except Exception:
                pass

        # 1. JSON 파일에서 추출 (예: duplicate_results_ahash_h16_ratio0p01_tol0p0039.json)
        for json_path in glob.glob("duplicate_results_*.json"):
            try:
                basename = os.path.splitext(os.path.basename(json_path))[0]
                parts = basename.split("_")
                # parts: ["duplicate", "results", method, "h{hash_size}", "ratio{ratio}", "tol{tolerance}"]
                if len(parts) >= 6 and parts[0] == "duplicate" and parts[1] == "results":
                    method = parts[2]
                    hash_size_str = parts[3]
                    ratio_str = parts[4]
                    tol_str = parts[5]
                    if hash_size_str.startswith("h") and ratio_str.startswith("ratio") and tol_str.startswith("tol"):
                        hash_size = int(hash_size_str[1:])
                        aspect_ratio_tol = float(ratio_str[5:].replace("p", "."))
                        tolerance_rate = float(tol_str[3:].replace("p", "."))
                        key = (method, hash_size, aspect_ratio_tol, tolerance_rate)
                        if key not in seen:
                            seen.add(key)
                            # 파일명에 groups 건수는 표시하지 않음 (로드 시 계산)
                            options.append(
                                (f"{method} / hash_size={hash_size} / tol={tolerance_rate}",
                                 "json", method, hash_size, aspect_ratio_tol, tolerance_rate)
                            )
            except Exception:
                pass

        # 2. DB 테이블에서 (method, hash_size) 추출 (JSON에 없는 것만)
        try:
            from ui_cache import _get_all_table_names
            from database import db_lock
            import sqlite3
            from compare import DB_FILE
            db_options = []
            with db_lock:
                conn = sqlite3.connect(DB_FILE, timeout=30)
                cur = conn.cursor()
                tables = _get_all_table_names(cur, "duplicate_results")
                for table in tables:
                    parts = table.split("_", 3)
                    if len(parts) >= 4:
                        method = parts[2]
                        try:
                            hash_size = int(parts[3])
                            # JSON에서 이미 처리된 (method, hash_size)는 제외
                            json_keys = {(m, h) for (_, t, m, h, *_ ) in options if t == "json"}
                            if (method, hash_size) not in json_keys:
                                db_options.append(
                                    (f"{method} / hash_size={hash_size}",
                                     "db", method, hash_size, None, None)
                                )
                        except ValueError:
                            pass
                conn.close()
            options.extend(db_options)
        except Exception:
            pass

        search_combo["values"] = [opt[0] for opt in options]
        search_combo._valid_options = options
        return options

    def on_search_selected(event=None):
        """드롭다운 선택 시 해당 결과 로드"""
        selected = search_combo.current()
        if selected < 0 or not hasattr(search_combo, '_valid_options'):
            return
        item = search_combo._valid_options[selected]
        source_type, method, hash_size, aspect_ratio_tol, tolerance_rate = item[1], item[2], item[3], item[4], item[5]

        # 실시간(진행 중) 선택 - 현재 검사 중인 옵션 기준
        if source_type == "live":
            groups = get_duplicate_groups()
            if groups:
                _populate_tree(groups, live_label=True)
                is_live_mode["value"] = True
                current_hash_opts["method"] = None
                current_hash_opts["hash_size"] = None
                current_hash_opts["aspect_ratio_tol"] = None
                current_hash_opts["tolerance_rate"] = None
            return

        # JSON 파일 선택 - 해당 결과의 method/hash_size 기준
        if source_type == "json":
            from results import _load_groups_from_json, duplicate_results_json_path
            json_path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)
            groups = _load_groups_from_json(json_path, method, hash_size, aspect_ratio_tol, tolerance_rate)
            if groups:
                _populate_tree(groups, live_label=False)
                is_live_mode["value"] = False
                current_hash_opts["method"] = method
                current_hash_opts["hash_size"] = hash_size
                current_hash_opts["aspect_ratio_tol"] = aspect_ratio_tol
                current_hash_opts["tolerance_rate"] = tolerance_rate
            return

        # DB 테이블 선택 - 해당 결과의 method/hash_size 기준
        if source_type == "db":
            from comparator import load_duplicate_results_from_db
            groups = load_duplicate_results_from_db(method, hash_size)
            if groups:
                _populate_tree(groups, live_label=False)
                is_live_mode["value"] = False
                current_hash_opts["method"] = method
                current_hash_opts["hash_size"] = hash_size
                current_hash_opts["aspect_ratio_tol"] = None
                current_hash_opts["tolerance_rate"] = None

    search_combo.bind("<<ComboboxSelected>>", on_search_selected)

    button_bar = tk.Frame(win)
    button_bar.pack(side="top", fill="x")

    # 상태표시줄 추가
    status_bar = tk.Label(win, text="그룹: 0 | 항목: 0 | 체크: 0", anchor="w", relief="sunken")
    status_bar.pack(side="bottom", fill="x")

    def update_status_bar():
        """상태표시줄 업데이트"""
        group_count = len(saved_groups)
        item_count = sum(len(group) for group in saved_groups)
        checked_count = sum(
            1 for group_id in tree.get_children()
            if is_checked(group_id) or any(is_checked(child_id) for child_id in tree.get_children(group_id))
        )
        status_bar.config(text=f"그룹: {group_count} | 항목: {item_count} | 체크: {checked_count}")

    paned = ttk.PanedWindow(win, orient="horizontal")
    paned.pack(fill="both", expand=True)

    tree_frame = tk.Frame(paned)
    detail_frame = tk.Frame(paned)

    paned.add(tree_frame, weight=1)
    paned.add(detail_frame, weight=1)

    # 창이 그려진 후 분할선(sash) 위치를 정확히 1:1 (중앙)로 유지
    # - 트리 컬럼 폭(requested size)이 커서 PanedWindow가 sash를 오른쪽으로
    #   밀어내는 문제를 방지하기 위해 최초 5초 동안 Configure 때마다 50% 재설정
    _sash_lock_until = {"end": 0}

    def _set_sash_center():
        try:
            win.update_idletasks()
            total_width = paned.winfo_width()
            if total_width > 50:
                paned.sashpos(0, total_width // 2)
        except Exception:
            pass

    def _on_win_configure(event):
        # 최초 표시 후 5초 동안은 창 크기 변경 시에도 좌우 1:1 유지
        if event.widget == win and time.time() < _sash_lock_until["end"]:
            win.after_idle(_set_sash_center)

    _sash_lock_until["end"] = time.time() + 5
    win.bind("<Configure>", _on_win_configure)
    win.after(100, _set_sash_center)
    win.after(300, _set_sash_center)
    win.after(500, _set_sash_center)

    tree_frame.grid_rowconfigure(0, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)

    tree = ttk.Treeview(tree_frame, columns=("checked", "count", "path"), show="tree headings")
    tree.heading("#0", text=lang["ui"].get("group_name", "그룹"))
    tree.heading("checked", text=lang["ui"].get("check", "선택"))
    tree.heading("count", text=lang["ui"].get("group_count", "항목 수"))
    tree.heading("path", text=lang["ui"].get("file_path", "파일 경로"))
    tree.column("#0", width=220, anchor="w", stretch=False)
    tree.column("checked", width=60, anchor="center", stretch=False)
    tree.column("count", width=80, anchor="center", stretch=False)
    tree.column("path", width=1500, minwidth=600, anchor="w", stretch=False)
    tree.grid(row=0, column=0, sticky="nsew")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.grid(row=0, column=1, sticky="ns")

    # 가로 스크롤바 추가 (트리뷰 바로 아래 row=1, col=0)
    h_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(xscrollcommand=h_scrollbar.set)
    h_scrollbar.grid(row=1, column=0, sticky="ew")

    preview_canvas = tk.Canvas(detail_frame, borderwidth=0, highlightthickness=0)
    preview_canvas.pack(fill="both", expand=True, side="left")

    preview_scrollbar = ttk.Scrollbar(detail_frame, orient="vertical", command=preview_canvas.yview)
    preview_scrollbar.pack(side="right", fill="y")
    preview_canvas.configure(yscrollcommand=preview_scrollbar.set)

    preview_inner = tk.Frame(preview_canvas)
    preview_window = preview_canvas.create_window((0, 0), window=preview_inner, anchor="nw")

    def _on_preview_config(event):
        preview_canvas.configure(scrollregion=preview_canvas.bbox("all"))

    def _on_canvas_config(event):
        preview_canvas.itemconfigure(preview_window, width=event.width)

    preview_inner.bind("<Configure>", _on_preview_config)
    preview_canvas.bind("<Configure>", _on_canvas_config)

    preview_images = []
    # 미리보기 이미지 캐시: {(path, mtime, size): PIL.Image} - 재클릭 시 재사용
    _preview_image_cache = {}
    _preview_image_cache_lock = threading.Lock()
    # 미리보기 세대 카운터: 빠른 연속 클릭 시 마지막 요청만 반영
    _preview_generation = {"value": 0}

    # ============================================================
    # 결과창 내부 복사본 (원본과 격리)
    # ============================================================
    # saved_groups: 결과창에서 작업하는 복사본 (원본에 영향 없음)
    # deleted_paths: 결과창에서 삭제/제거된 파일 경로 (창 닫기 시 원본에 반영)
    saved_groups = []
    all_group_tree_nodes = []  # [(parent_id, [child_id, ...]), ...] 구조를 보관하여 필터링 시 reattach 복원용
    deleted_paths = set()  # 실제 파일 삭제 + 목록 제거된 경로
    is_live_mode = {"value": False}  # 실시간 모드 여부
    # 사용자가 체크 작업 중일 때 실시간 갱신을 유예하는 쿨다운 시각 (time.time() 기준)
    live_refresh_cooldown_until = {"value": 0.0}
    # 현재 표시 중인 검색 결과의 해시 옵션 (드롭다운 선택 결과 기준)
    # - JSON/DB 선택: 해당 결과의 method/hash_size
    # - live/load_results: None (resolve_search_options() 사용)
    current_hash_opts = {
        "method": None,
        "hash_size": None,
        "aspect_ratio_tol": None,
        "tolerance_rate": None,
    }

    def is_checked(item_id):
        return tree.set(item_id, "checked") == "☑"

    def set_checked(item_id, checked):
        tree.set(item_id, "checked", "☑" if checked else "☐")

    def toggle_checked(item_id):
        # 체크 작업 중 실시간 새로고침이 방해하지 않도록 5초간 갱신 유예
        live_refresh_cooldown_until["value"] = time.time() + 5
        current = is_checked(item_id)
        set_checked(item_id, not current)
        if tree.parent(item_id) == "":
            for child_id in tree.get_children(item_id):
                set_checked(child_id, not current)
        else:
            parent_id = tree.parent(item_id)
            children = tree.get_children(parent_id)
            if children:
                set_checked(parent_id, all(is_checked(child) for child in children))
        update_status_bar()

    def _get_group_index(item_id):
        """태그로 저장된 saved_groups 인덱스 조회 (필터로 인한 표시 순서와 무관)"""
        tags = tree.item(item_id, "tags")
        if len(tags) >= 2 and tags[0] == "group":
            return int(tags[1])
        return tree.index(item_id)

    def get_checked_items():
        checked_groups = set()
        checked_files = []
        for group_id in tree.get_children():
            if is_checked(group_id):
                checked_groups.add(_get_group_index(group_id))
            else:
                for child_id in tree.get_children(group_id):
                    if is_checked(child_id):
                        checked_files.append((tree.set(child_id, "path"), group_id))
        return checked_groups, checked_files

    def open_folder_for_file(file_path):
        folder_path = os.path.dirname(file_path)
        if os.path.isdir(folder_path):
            os.startfile(folder_path)

    def open_file(file_path):
        if os.path.isfile(file_path):
            os.startfile(file_path)

    def tree_open_item(item_id, open_file_only):
        if not item_id:
            return
        parent_id = tree.parent(item_id)
        if parent_id:
            path = tree.set(item_id, "path")
            if not path:
                return
            if open_file_only:
                open_file(path)
            else:
                open_folder_for_file(path)
        else:
            group_index = tree.index(item_id)
            if saved_groups and 0 <= group_index < len(saved_groups):
                first_path = saved_groups[group_index][0]
                open_folder_for_file(first_path)

    def _current_results_path():
        """현재 표시 중인 검색 결과의 JSON 파일 경로"""
        if current_hash_opts["method"] and current_hash_opts["hash_size"]:
            method = current_hash_opts["method"]
            hash_size = current_hash_opts["hash_size"]
            aspect_ratio_tol = current_hash_opts.get("aspect_ratio_tol")
            tolerance_rate = current_hash_opts.get("tolerance_rate")
        else:
            method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
        return duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)

    def save_duplicate_groups_json(groups):
        """복사본을 JSON 파일로 저장 (현재 표시 중인 검색 결과 기준)"""
        if current_hash_opts["method"] and current_hash_opts["hash_size"]:
            method = current_hash_opts["method"]
            hash_size = current_hash_opts["hash_size"]
            aspect_ratio_tol = current_hash_opts.get("aspect_ratio_tol")
            tolerance_rate = current_hash_opts.get("tolerance_rate")
        else:
            method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
        data = {
            "saved_at": datetime.now().isoformat(),
            "search_options": {
                "method": method,
                "hash_size": hash_size,
                "aspect_ratio_tol": aspect_ratio_tol,
                "tolerance_rate": tolerance_rate,
            },
            "groups": groups,
        }
        with open(_current_results_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _save_groups_to_db(groups):
        """현재 표시 중인 검색 결과 기준으로 DB 중복 결과 테이블에 그룹 저장 (동기)"""
        if not groups:
            return
        try:
            if current_hash_opts["method"] and current_hash_opts["hash_size"]:
                method = current_hash_opts["method"]
                hash_size = current_hash_opts["hash_size"]
            else:
                method, hash_size, _ratio, _tol = resolve_search_options()
            from database import _table_name, db_lock
            import sqlite3
            from compare import DB_FILE
            results_table = _table_name("duplicate_results", method, hash_size)
            with db_lock:
                conn = sqlite3.connect(DB_FILE, timeout=30)
                try:
                    cur = conn.cursor()
                    cur.execute(f"DELETE FROM {results_table}")
                    for group_id, group in enumerate(groups):
                        for path in sorted(group):
                            cur.execute(
                                f"REPLACE INTO {results_table} (group_id, path) VALUES (?,?)",
                                (group_id, path)
                            )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass

    _chunk_job_id = {"id": None}

    def _cancel_chunk_job():
        if _chunk_job_id["id"] is not None:
            try:
                win.after_cancel(_chunk_job_id["id"])
            except Exception:
                pass
            _chunk_job_id["id"] = None

    def _populate_tree(groups, live_label=False):
        """트리를 groups 데이터로 채우고 saved_groups 동기화 (청크 분할 주입으로 UI 프리징 방지)"""
        nonlocal saved_groups, all_group_tree_nodes
        _cancel_chunk_job()
        groups = _filter_groups_by_folder(groups)
        saved_groups = [list(g) for g in groups] if groups else []
        tree.delete(*tree.get_children())
        all_group_tree_nodes.clear()

        if not saved_groups:
            update_status_bar()
            display_preview_for_group([])
            return

        label_suffix = " (실시간)" if live_label else ""
        total_len = len(saved_groups)
        chunk_size = 200  # 한 번에 200그룹씩 주입
        first_item = [None]

        def _insert_chunk(start_idx):
            end_idx = min(start_idx + chunk_size, total_len)
            for i in range(start_idx, end_idx):
                group = saved_groups[i]
                gi = i + 1
                parent_id = tree.insert("", "end", text=f"Group {gi}{label_suffix}", values=("☐", len(group), ""), open=True, tags=("group", i))
                child_ids = []
                for file_path in group:
                    child_id = tree.insert(parent_id, "end", text=os.path.basename(file_path), values=("☐", "", file_path), tags=("item",), open=False)
                    child_ids.append(child_id)
                    if first_item[0] is None:
                        first_item[0] = child_id
                all_group_tree_nodes.append((parent_id, child_ids))

            # 첫 번째 청크 삽입 직후 즉시 미리보기 및 선택 설정 (초기 응답성)
            if start_idx == 0 and saved_groups:
                display_preview_for_group(saved_groups[0])
                if first_item[0] is not None:
                    tree.selection_set(first_item[0])
                    tree.focus(first_item[0])

            _apply_path_filter()
            update_status_bar()

            # 남은 청크가 있으면 다음 프레임에 예약
            if end_idx < total_len:
                _chunk_job_id["id"] = win.after(1, lambda: _insert_chunk(end_idx))
            else:
                _chunk_job_id["id"] = None

        _insert_chunk(0)

    def _apply_path_filter():
        """파일 경로 필터 + 폴더 모드 드롭다운 + 그룹 체크 상태 라디오버튼에 따라 트리 항목 표시/숨김"""
        keyword = path_filter_var.get().strip().lower()
        view_mode = group_view_var.get() if "group_view_var" in dir() else "all"
        folder_mode = folder_filter_var.get() if "folder_filter_var" in dir() else "전체 보기"

        for group_id, child_ids in all_group_tree_nodes:
            group_visible = False
            has_checked = False
            group_dirs = set()

            # 자식 항목 검사
            for child_id in child_ids:
                path = tree.set(child_id, "path") or ""
                path_lower = path.lower()
                if path:
                    group_dirs.add(os.path.dirname(path))

                is_chk = is_checked(child_id)
                if is_chk:
                    has_checked = True

                # 키워드 필터
                match_keyword = not keyword or (keyword in path_lower)
                if not match_keyword:
                    tree.detach(child_id)
                else:
                    tree.reattach(child_id, group_id, "end")
                    group_visible = True

            # 1. 라디오버튼 보기 모드 필터 (체크 여부)
            match_view = True
            if view_mode == "has_checked":
                match_view = has_checked
            elif view_mode == "no_checked":
                match_view = not has_checked

            # 2. 폴더 필터 (전체 보기 / 폴더 간 중복만 / 폴더 내 중복만)
            match_folder = True
            if folder_mode == "폴더 간 중복만":
                match_folder = (len(group_dirs) > 1)
            elif folder_mode == "폴더 내 중복만":
                match_folder = (len(group_dirs) == 1)

            # 그룹에 표시할 항목이 없거나 조건에 맞지 않으면 그룹 전체 detach, 맞으면 reattach
            if not group_visible or not match_view or not match_folder:
                tree.detach(group_id)
            else:
                tree.reattach(group_id, "", "end")

        update_status_bar()

    def load_results():
        """저장된 결과를 로드하여 복사본으로 작업"""
        method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
        groups = load_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
        if groups is None:
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("no_saved_results", "저장된 중복 검색 결과가 없습니다."), parent=win)
            win.lift()
            win.focus_force()
            return []
        is_live_mode["value"] = False
        # 현재 config 기준 결과이므로 해시 옵션 리셋 (resolve_search_options() 사용)
        current_hash_opts["method"] = None
        current_hash_opts["hash_size"] = None
        current_hash_opts["aspect_ratio_tol"] = None
        current_hash_opts["tolerance_rate"] = None
        _populate_tree(groups, live_label=False)
        return saved_groups

    def _get_file_hash_text(file_path):
        """
        현재 표시 중인 검색 결과의 해시 옵션 기준 파일 해시 문자열 반환.

        - 드롭다운에서 선택한 검색 결과의 method/hash_size를 사용 (current_hash_opts)
        - live/load_results(현재 config 기준)인 경우 resolve_search_options() 사용
        - 해시를 오른쪽부터(역순) 표시: 이미지 해시는 왼쪽(MSB)이 이미지 전체
          특성을 나타내어 유사 이미지끼리 거의 같고, 오른쪽(LSB) 끝부분이 세부
          차이를 나타내므로 중복 비교 시 오른쪽 값이 더 유용하다.
        """
        try:
            from hasher import get_cached_file_hash
            if current_hash_opts["method"] and current_hash_opts["hash_size"]:
                method = current_hash_opts["method"]
                hash_size = current_hash_opts["hash_size"]
            else:
                method, hash_size, _ratio, _tol = resolve_search_options()
            h = get_cached_file_hash((file_path, method, hash_size))
            return str(h)[::-1] if h else None  # 오른쪽부터 표시
        except Exception:
            return None

    def _on_show_hash_toggle():
        """해시 보여주기 체크박스 토글 시 현재 미리보기를 갱신"""
        try:
            show_selected_preview()
        except Exception:
            pass

    def display_preview_for_group(group):
        """
        그룹 미리보기 표시 (비동기 이미지 로드).

        개선 사항:
        - 헤더/프레임은 UI 스레드에서 즉시 생성 → 클릭 반응 즉시
        - 이미지 디코딩/리사이즈는 백그라운드 스레드에서 수행 → UI 블로킹 없음
        - ImageTk.PhotoImage 생성은 Tk 스레드 안전성을 위해 win.after로 UI 스레드에 위임
        - 세대(generation) 카운터로 연속 클릭 시 마지막 요청만 반영
        - _preview_image_cache로 같은 파일 재표시 시 즉시 로드
        """
        nonlocal preview_images
        for child in preview_inner.winfo_children():
            child.destroy()
        preview_images = []
        preview_canvas.yview_moveto(0)  # 스크롤 위치 초기화

        if not group:
            return

        # 새 세대 증가 (이전 백그라운드 로드 무효화)
        _preview_generation["value"] += 1
        generation = _preview_generation["value"]

        # 각 파일의 프레임/헤더를 즉시 생성
        show_hash = show_hash_var.get()
        frame_map = {}  # frame_id -> (frame, file_path, img_label, hash_label)
        for file_path in group:
            frame = tk.Frame(preview_inner, bd=1, relief="solid", padx=4, pady=4)
            frame.pack(fill="x", pady=2, padx=2)

            header_row = tk.Frame(frame)
            header_row.pack(fill="x")

            title = tk.Label(header_row, text=os.path.basename(file_path), anchor="w", fg="black")
            title.grid(row=0, column=0, sticky="w")
            title.bind("<Double-1>", lambda event, path=file_path: open_file(path))
            title.bind("<Button-3>", lambda event, path=file_path: open_file(path))

            folder_label = tk.Label(header_row, text=os.path.dirname(file_path), anchor="w", fg="gray")
            folder_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
            folder_label.bind("<Double-1>", lambda event, path=file_path: open_folder_for_file(path))
            folder_label.bind("<Button-3>", lambda event, path=file_path: open_folder_for_file(path))

            header_row.grid_columnconfigure(0, weight=0)
            header_row.grid_columnconfigure(1, weight=1)

            # 해시 라벨 (폴더경로 아래) - '해시 보여주기' 체크 시에만 생성
            hash_label = None
            if show_hash:
                hash_label = tk.Label(frame, text="해시: 계산 중...", anchor="w", fg="blue")
                hash_label.pack(fill="x")

            if not os.path.exists(file_path):
                label = tk.Label(frame, text=lang["ui"].get("missing_file", "파일을 찾을 수 없습니다."), fg="red")
                label.pack(fill="x")
                continue

            # 로딩 표시 먼저
            loading_label = tk.Label(frame, text="로딩 중...", fg="gray")
            loading_label.pack(fill="x")

            img_label = tk.Label(frame)
            frame_map[id(frame)] = (frame, file_path, img_label, loading_label, hash_label)

        if not frame_map:
            return

        # 백그라운드 스레드에서 이미지 디코딩/리사이즈 + 해시 계산 (UI 블로킹 방지)
        def load_images_async():
            # 각 프레임에 대해 PIL 이미지 준비 (디코딩 - CPU/IO 무거운 작업)
            prepared = []  # [(frame_id, pil_image_or_None, hash_text_or_None)]
            for frame_id, (frame, file_path, img_label, loading_label, hash_label) in frame_map.items():
                if _preview_generation["value"] != generation:
                    return  # 최신 세대 아님 - 버림
                # 해시 계산 (선택 사항: 체크박스 켜짐 시에만)
                hash_text = None
                if hash_label is not None:
                    hash_text = _get_file_hash_text(file_path)
                try:
                    stat = os.stat(file_path)
                    cache_key = (file_path, stat.st_mtime, stat.st_size)
                    with _preview_image_cache_lock:
                        pil_img = _preview_image_cache.get(cache_key)
                    if pil_img is None:
                        pil_img = Image.open(file_path)
                        pil_img.thumbnail((300, 300))
                        # PIL 이미지는 스레드 간 공유 가능, PhotoImage는 Tk 스레드에서만
                        with _preview_image_cache_lock:
                            # 캐시 크기 제한 (200개 초과 시 오래된 것 제거)
                            if len(_preview_image_cache) > 200:
                                _preview_image_cache.clear()
                            _preview_image_cache[cache_key] = pil_img
                except Exception:
                    pil_img = None
                prepared.append((frame_id, pil_img, hash_text))

            if _preview_generation["value"] != generation:
                return

            # UI 스레드에서 PhotoImage 생성 및 표시
            def apply_images():
                if _preview_generation["value"] != generation:
                    return
                for frame_id, pil_img, hash_text in prepared:
                    item = frame_map.get(frame_id)
                    if item is None:
                        continue
                    frame, file_path, img_label, loading_label, hash_label = item
                    loading_label.destroy()
                    # 해시 라벨 업데이트 (폴더경로 아래)
                    if hash_label is not None:
                        try:
                            if hash_label.winfo_exists():
                                if hash_text:
                                    hash_label.config(text=f"해시: {hash_text}")
                                else:
                                    hash_label.config(text="해시: (조회 실패)", fg="red")
                        except Exception:
                            pass
                    try:
                        if pil_img is None:
                            raise Exception("load failed")
                        photo = ImageTk.PhotoImage(pil_img)
                        preview_images.append(photo)
                        img_label.configure(image=photo)
                        img_label.image = photo
                        img_label.pack()
                        img_label.bind("<Double-1>", lambda event, path=file_path: open_file(path))
                        img_label.bind("<Button-3>", lambda event, path=file_path: open_folder_for_file(path))
                    except Exception:
                        err_label = tk.Label(frame, text=lang["ui"].get("preview_error", "미리보기를 로드할 수 없습니다."), fg="red")
                        err_label.pack(fill="x")
            try:
                win.after(0, apply_images)
            except Exception:
                pass

        threading.Thread(target=load_images_async, daemon=True).start()

    def show_selected_preview(event=None):
        """선택된 트리 항목의 그룹 미리보기 표시 (saved_groups 기준)"""
        selected = tree.selection()
        if not selected:
            return
        item_id = selected[0]
        parent_id = tree.parent(item_id)
        if parent_id:
            group_index = _get_group_index(parent_id)
        else:
            group_index = _get_group_index(item_id)
        # saved_groups 범위 체크 후 미리보기
        if 0 <= group_index < len(saved_groups):
            display_preview_for_group(saved_groups[group_index])

    def on_tree_click(event):
        item_id = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not item_id or col != "#1":
            return
        toggle_checked(item_id)
        return "break"

    def on_tree_double_click(event):
        item_id = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not item_id:
            return
        if col == "#3":
            tree_open_item(item_id, open_file_only=False)
        elif col == "#0":
            tree_open_item(item_id, open_file_only=True)

    def on_tree_right_click(event):
        item_id = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not item_id:
            return
        if col == "#3":
            tree_open_item(item_id, open_file_only=False)
        elif col == "#0":
            tree_open_item(item_id, open_file_only=True)

    def on_tree_space(event):
        item_id = tree.focus()
        if item_id:
            toggle_checked(item_id)
        return "break"

    def remove_missing_files():
        """존재하지 않는 파일을 복사본에서 제거"""
        if not saved_groups:
            return
        changed = False
        new_groups = []
        missing_paths = []
        for group in saved_groups:
            remaining = []
            for p in group:
                if os.path.exists(p):
                    remaining.append(p)
                else:
                    missing_paths.append(p)
                    deleted_paths.add(p)
                    changed = True
            if len(remaining) > 1:
                new_groups.append(remaining)
        if changed:
            # DB 캐시에서도 존재하지 않는 파일 제거 (현재 표시 중인 검색 결과 기준)
            if current_hash_opts["method"] and current_hash_opts["hash_size"]:
                method = current_hash_opts["method"]
                hash_size = current_hash_opts["hash_size"]
            else:
                method, hash_size, _ratio, _tol = resolve_search_options()
            remove_missing_files_from_cache(method, hash_size, missing_paths)
            _save_groups_to_db(new_groups)
            save_duplicate_groups_json(new_groups)
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("removed_missing", "없는 파일 목록이 제거되었습니다."), parent=win)
            win.lift()
            win.focus_force()
            _populate_tree(new_groups, live_label=False)

    def remove_selected_items():
        """선택된 항목을 복사본에서 제거 (전체 트리 재구성을 하지 않는 증분 제거로 0.01초 즉시 처리)"""
        checked_groups, checked_files = get_checked_items()
        if not checked_groups and not checked_files:
            return

        # 1. 제거할 트리의 노드 ID들 수집
        tree_nodes_to_delete = set()
        group_indices_to_clear = set(checked_groups)

        # 체크된 그룹 노드
        for group_idx in group_indices_to_clear:
            if 0 <= group_idx < len(all_group_tree_nodes):
                parent_id, _ = all_group_tree_nodes[group_idx]
                tree_nodes_to_delete.add(parent_id)
                if group_idx < len(saved_groups):
                    for p in saved_groups[group_idx]:
                        deleted_paths.add(p)
                    saved_groups[group_idx] = []

        # 체크된 개별 파일 노드
        for file_path, group_id in checked_files:
            group_idx = _get_group_index(group_id)
            if 0 <= group_idx < len(saved_groups):
                if file_path in saved_groups[group_idx]:
                    saved_groups[group_idx].remove(file_path)
                    deleted_paths.add(file_path)

                # 그 그룹의 자식 노드 중 해당 파일 노드 찾기
                for child_id in tree.get_children(group_id):
                    if tree.set(child_id, "path") == file_path:
                        tree_nodes_to_delete.add(child_id)

                # 그룹 내 남아있는 파일이 1개 이하가 되면 해당 그룹 전체 제거 대상 추가
                if len(saved_groups[group_idx]) <= 1:
                    tree_nodes_to_delete.add(group_id)
                    for remaining_p in saved_groups[group_idx]:
                        deleted_paths.add(remaining_p)
                    saved_groups[group_idx] = []

        # 2. 트리에서 대상 노드만 빠르게 즉시 제거 (전체 리빌드 없음)
        for node_id in tree_nodes_to_delete:
            try:
                tree.delete(node_id)
            except Exception:
                pass

        # 3. 비동기/배경으로 DB + JSON 파일 업데이트 (UI 동결 방지)
        new_groups = [g for g in saved_groups if len(g) > 1]
        threading.Thread(
            target=lambda: (_save_groups_to_db(new_groups), save_duplicate_groups_json(new_groups)),
            daemon=True
        ).start()

        update_status_bar()

    def delete_selected_files():
        """선택된 파일을 실제로 삭제하고 복사본에서도 제거 (백그라운드 삭제 + 증분 제거)"""
        checked_groups, checked_files = get_checked_items()
        if not checked_groups and not checked_files:
            return
        confirm_msg = lang["ui"].get(
            "delete_files_confirm",
            "선택한 파일을 실제로 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.",
        )
        if not messagebox.askyesno(lang["ui"].get("confirm", "확인"), confirm_msg, parent=win, default=messagebox.NO):
            win.lift()
            win.focus_force()
            return
        win.lift()
        win.focus_force()

        paths_to_delete = []
        for group_index in checked_groups:
            if 0 <= group_index < len(saved_groups):
                paths_to_delete.extend(saved_groups[group_index])
        for file_path, _group_id in checked_files:
            if file_path not in paths_to_delete:
                paths_to_delete.append(file_path)

        failed = []
        for file_path in paths_to_delete:
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    deleted_paths.add(file_path)
            except OSError:
                failed.append(file_path)

        remove_selected_items()

        if failed:
            messagebox.showwarning(
                lang["ui"].get("info", "정보"),
                lang["ui"].get("delete_files_error", "일부 파일을 삭제하지 못했습니다.") + "\n" + "\n".join(failed),
                parent=win,
            )
            win.lift()
            win.focus_force()

    def invert_selection():
        # 일괄 체크 작업 중 실시간 새로고침 유예
        live_refresh_cooldown_until["value"] = time.time() + 5
        for group_id in tree.get_children():
            current_group = is_checked(group_id)
            set_checked(group_id, not current_group)
            for child_id in tree.get_children(group_id):
                set_checked(child_id, not is_checked(child_id))
        update_status_bar()

    def select_all():
        """모든 그룹/항목 체크"""
        # 일괄 체크 작업 중 실시간 새로고침 유예
        live_refresh_cooldown_until["value"] = time.time() + 5
        for group_id in tree.get_children():
            set_checked(group_id, True)
            for child_id in tree.get_children(group_id):
                set_checked(child_id, True)
        update_status_bar()

    def deselect_all():
        """모든 그룹/항목 체크 해제"""
        # 일괄 체크 해제 작업 중 실시간 새로고침 유예
        live_refresh_cooldown_until["value"] = time.time() + 5
        for group_id in tree.get_children():
            set_checked(group_id, False)
            for child_id in tree.get_children(group_id):
                set_checked(child_id, False)
        update_status_bar()

    remove_missing_btn = tk.Button(button_bar, text=lang["ui"].get("remove_missing", "없는파일 목록에서 제거"), command=remove_missing_files)
    remove_missing_btn.pack(side="left")
    add_tooltip(remove_missing_btn, lang["ui"].get("tooltip_remove_missing", ""))

    remove_selected_btn = tk.Button(button_bar, text=lang["ui"].get("remove_selected", "체크한 그룹/항목 목록에서 제거"), command=remove_selected_items)
    remove_selected_btn.pack(side="left")
    add_tooltip(remove_selected_btn, lang["ui"].get("tooltip_remove_selected", ""))

    delete_selected_btn = tk.Button(button_bar, text=lang["ui"].get("delete_selected_items", "선택 항목 실제 파일 삭제"), command=delete_selected_files)
    delete_selected_btn.pack(side="left")
    add_tooltip(delete_selected_btn, lang["ui"].get("tooltip_delete_selected", ""))

    select_all_btn = tk.Button(button_bar, text=lang["ui"].get("select_all", "전체선택"), command=select_all)
    select_all_btn.pack(side="left")
    add_tooltip(select_all_btn, lang["ui"].get("tooltip_select_all", ""))

    deselect_all_btn = tk.Button(button_bar, text=lang["ui"].get("deselect_all", "전체해제"), command=deselect_all)
    deselect_all_btn.pack(side="left")
    add_tooltip(deselect_all_btn, lang["ui"].get("tooltip_deselect_all", ""))

    invert_btn = tk.Button(button_bar, text=lang["ui"].get("invert_selection", "선택 항목 반전"), command=invert_selection)
    invert_btn.pack(side="left")
    add_tooltip(invert_btn, lang["ui"].get("tooltip_invert_selection", ""))

    def refresh_results():
        """새로고침: 삭제/제거한 항목을 원본에 반영 후 원본에서 다시 복사해오기"""
        # 1. 삭제/제거된 항목을 원본에 반영 (현재 표시 중인 검색 결과 기준)
        apply_changes_on_close()
        # 2. deleted_paths 초기화 (이미 반영했으므로)
        deleted_paths.clear()

        # 3. 현재 표시 중이던 검색 결과 기준으로 다시 로드 (config 결과로 덮어쓰지 않도록)
        if current_hash_opts["method"] and current_hash_opts["hash_size"]:
            method = current_hash_opts["method"]
            hash_size = current_hash_opts["hash_size"]
            aspect_ratio_tol = current_hash_opts.get("aspect_ratio_tol")
            tolerance_rate = current_hash_opts.get("tolerance_rate")
            if aspect_ratio_tol is not None and tolerance_rate is not None:
                # JSON 결과 다시 로드
                from results import _load_groups_from_json
                json_path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)
                groups = _load_groups_from_json(json_path, method, hash_size, aspect_ratio_tol, tolerance_rate)
            else:
                # DB 결과 다시 로드
                from comparator import load_duplicate_results_from_db
                groups = load_duplicate_results_from_db(method, hash_size)
            if groups:
                is_live_mode["value"] = False
                _populate_tree(groups, live_label=False)
            else:
                load_results()
        else:
            # 현재 config 기준 결과
            load_results()
        logger.info("[bold cyan][알림] 결과창 새로고침 완료[/bold cyan]")

    refresh_btn = tk.Button(button_bar, text=lang["ui"].get("refresh_results", "새로고침"), command=refresh_results)
    refresh_btn.pack(side="left")
    add_tooltip(refresh_btn, lang["ui"].get("tooltip_refresh_results", ""))

    def on_delete_key(event):
        remove_selected_items()
        return "break"

    def on_shift_delete_key(event):
        delete_selected_files()
        return "break"

    tree.bind("<<TreeviewSelect>>", show_selected_preview)
    tree.bind("<Button-1>", on_tree_click)
    tree.bind("<Double-1>", on_tree_double_click)
    tree.bind("<Button-3>", on_tree_right_click)
    tree.bind("<space>", on_tree_space)
    tree.bind("<Delete>", on_delete_key)
    tree.bind("<Shift-Delete>", on_shift_delete_key)
    win.bind("<Delete>", on_delete_key)
    win.bind("<Shift-Delete>", on_shift_delete_key)

    # ============================================================
    # 실시간 중복 그룹 갱신 (백그라운드 스레드 + UI 스레드 분리)
    # - '자동 재갱신' 체크박스(auto_refresh_var)로 제어됨
    # - 기본값: 해제 (수동 새로고침만 동작)
    # ============================================================
    live_refresh_enabled = {"value": False}
    live_refresh_after_id = {"id": None}
    last_group_count = {"value": 0}  # 마지막으로 확인한 그룹 수

    def _on_auto_refresh_toggle():
        """자동 재갱신 체크박스 토글 시 실시간 폴링 시작/중지"""
        if auto_refresh_var.get():
            # 체크: 실시간 폴링 시작 (이미 실행 중이면 중복 예약 방지)
            if not live_refresh_enabled["value"]:
                live_refresh_enabled["value"] = True
                if live_refresh_after_id["id"] is None:
                    refresh_live_groups()
        else:
            # 해제: 실시간 폴링 중지
            stop_live_refresh()
        logger.info(f"[bold cyan][알림] 자동 재갱신 {'활성화' if auto_refresh_var.get() else '비활성화'}[/bold cyan]")

    def refresh_live_groups():
        """진행 중인 비교의 중복 그룹을 실시간으로 트리에 반영 (백그라운드)"""
        if not live_refresh_enabled["value"]:
            return

        # 사용자가 체크 작업 중이면 갱신 유예 (체크 상태가 초기화되지 않도록)
        if time.time() < live_refresh_cooldown_until["value"]:
            live_refresh_after_id["id"] = win.after(2000, refresh_live_groups)
            return

        # 백그라운드 스레드에서 get_duplicate_groups() 실행 (UI 블로킹 방지)
        def fetch_groups():
            if not live_refresh_enabled["value"]:
                return
            try:
                if not is_stop_requested():
                    groups = get_duplicate_groups()
                    if groups and len(groups) != last_group_count["value"]:
                        # UI 스레드에서 트리 갱신 (미리보기는 첫 그룹만 갱신하지 않음)
                        def update_ui():
                            try:
                                is_live_mode["value"] = True
                                # 실시간 갱신 시 미리보기 유지 (사용자가 보고 있는 그룹이
                                # 재구성으로 사라지지 않도록 _populate_tree의
                                # display_preview_for_group 호출 방지)
                                _live_populate_tree(groups, live_label=True)
                                last_group_count["value"] = len(groups)
                            except Exception:
                                pass
                        win.after(0, update_ui)
            except Exception:
                pass

        threading.Thread(target=fetch_groups, daemon=True).start()

        # 다음 폴링 예약 (2초 간격 - UI 부하 감소)
        live_refresh_after_id["id"] = win.after(2000, refresh_live_groups)

    def _live_populate_tree(groups, live_label=False):
        """실시간 갱신용 트리 구성 (청크 분할 및 선택 유지)"""
        nonlocal saved_groups, all_group_tree_nodes
        _cancel_chunk_job()
        groups = _filter_groups_by_folder(groups)

        # 재구성 전 현재 체크 상태를 경로 기준으로 백업 (체크 초기화 방지)
        checked_paths = set()
        for _group_id in tree.get_children():
            for _child_id in tree.get_children(_group_id):
                if is_checked(_child_id):
                    _p = tree.set(_child_id, "path")
                    if _p:
                        checked_paths.add(_p)
            if is_checked(_group_id):
                for _child_id in tree.get_children(_group_id):
                    _p = tree.set(_child_id, "path")
                    if _p:
                        checked_paths.add(_p)

        selected_preview_group = None
        selected = tree.selection()
        if selected:
            item_id = selected[0]
            parent_id = tree.parent(item_id)
            if parent_id:
                idx = _get_group_index(parent_id)
            else:
                idx = _get_group_index(item_id)
            if 0 <= idx < len(saved_groups):
                selected_preview_group = saved_groups[idx]

        saved_groups = [list(g) for g in groups] if groups else []
        tree.delete(*tree.get_children())
        all_group_tree_nodes.clear()

        if not saved_groups:
            update_status_bar()
            return

        label_suffix = " (실시간)" if live_label else ""
        total_len = len(saved_groups)
        chunk_size = 200

        def _insert_live_chunk(start_idx):
            end_idx = min(start_idx + chunk_size, total_len)
            for i in range(start_idx, end_idx):
                group = saved_groups[i]
                gi = i + 1
                parent_id = tree.insert("", "end", text=f"Group {gi}{label_suffix}", values=("☐", len(group), ""), open=True, tags=("group", i))
                child_ids = []
                for file_path in group:
                    child_id = tree.insert(parent_id, "end", text=os.path.basename(file_path), values=("☐", "", file_path), tags=("item",), open=False)
                    child_ids.append(child_id)
                    # 경로 기준으로 이전 체크 상태 복원
                    if file_path in checked_paths:
                        set_checked(child_id, True)
                # 그룹의 모든 자식이 체크되었으면 그룹도 체크
                if child_ids and all(is_checked(cid) for cid in child_ids):
                    set_checked(parent_id, True)
                all_group_tree_nodes.append((parent_id, child_ids))

            _apply_path_filter()
            update_status_bar()

            if end_idx < total_len:
                _chunk_job_id["id"] = win.after(1, lambda: _insert_live_chunk(end_idx))
            else:
                _chunk_job_id["id"] = None
                if selected_preview_group is not None:
                    for gi, group in enumerate(saved_groups):
                        if group == selected_preview_group:
                            try:
                                first_child = tree.get_children(all_group_tree_nodes[gi][0])[0]
                                tree.selection_set(first_child)
                                tree.focus(first_child)
                            except Exception:
                                pass
                            break

        _insert_live_chunk(0)

    def stop_live_refresh():
        """실시간 갱신 중지"""
        live_refresh_enabled["value"] = False
        if live_refresh_after_id["id"] is not None:
            try:
                win.after_cancel(live_refresh_after_id["id"])
            except Exception:
                pass
            live_refresh_after_id["id"] = None

    # ============================================================
    # 외부(run.py)에서 호출 가능한 인터페이스
    # - run.py에서 자동 재시도가 발생할 때 이 함수를 호출하여
    #   '자동 재갱신' 체크박스가 켜져 있으면 결과를 자동으로 재로드
    # ============================================================
    def _notify_compare_retry():
        """자동 재시도 후 결과 자동 갱신 (체크박스가 켜져 있을 때만)"""
        if auto_refresh_var.get():
            def _do_auto_refresh():
                try:
                    # 1. 삭제/제거된 항목을 원본에 반영
                    apply_changes_on_close()
                    # 2. deleted_paths 초기화 (이미 반영했으므로)
                    deleted_paths.clear()
                    # 3. 원본에서 다시 복사본 가져오기
                    load_results()
                    logger.info("[bold cyan][알림] 자동 재시도 후 결과창 자동 갱신 완료[/bold cyan]")
                except Exception as e:
                    logger.error(f"[결과 자동 갱신 오류] {e}")
            try:
                win.after(0, _do_auto_refresh)
            except Exception:
                pass

    # run.py에서 접근할 수 있도록 win 객체에 콜백 노출
    win.notify_compare_retry = _notify_compare_retry

    def apply_changes_on_close():
        """창 닫기 시 삭제/제거된 파일을 원본 결과에 반영"""
        if deleted_paths:
            try:
                if current_hash_opts["method"] and current_hash_opts["hash_size"]:
                    method = current_hash_opts["method"]
                    hash_size = current_hash_opts["hash_size"]
                else:
                    method, hash_size, _ratio, _tol = resolve_search_options()
                count = len(deleted_paths)
                # DB 캐시에서 삭제된 파일 제거
                remove_missing_files_from_cache(method, hash_size, list(deleted_paths))
                # 수정된 그룹을 DB 및 JSON에 저장 (선택된 검색 결과 기준)
                new_groups = [list(g) for g in saved_groups if len(g) > 1]
                if new_groups:
                    _save_groups_to_db(new_groups)
                    save_duplicate_groups_json(new_groups)
                deleted_paths.clear()  # 중복 반영 방지를 위해 초기화
                logger.info(f"[bold cyan][알림] 결과창 변경사항 반영: {count}개 파일 제거됨[/bold cyan]")
            except Exception as e:
                logger.error(f"[결과창 반영 오류] {e}")

    # 검색 결과 목록 초기화
    valid_options = refresh_search_list()
    if valid_options:
        # 기본값: 첫 번째 항목 (또는 현재 검사 진행 중인 것)
        search_combo.current(0)
        on_search_selected()
    else:
        # 결과가 없으면 기존 load_results() 시도
        load_results()

    # 실시간 갱신 시작
    refresh_live_groups()

    def _on_destroy(event):
        # 자식 위젯 파괴 이벤트로 인한 중복 호출 방지 (최상위 win 파괴 시에만 실행)
        if event.widget == win:
            stop_live_refresh()
            apply_changes_on_close()

    # 창이 닫힐 때: 실시간 갱신 중지 + 변경사항 원본 반영
    win.bind("<Destroy>", _on_destroy)

    return win
