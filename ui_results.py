"""
중복 결과 창 모듈.

- 결과창은 원본 중복 결과의 복사본으로 작업 (검사 엔진과 격리)
- 실시간 갱신 시 saved_groups 동기화
- 창 닫기 시 삭제/제거 내용을 원본에 반영 (JSON 저장 + DB 캐시 정리)
"""

import os
import json
import copy
import threading
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


def show_duplicate_results_window(root, lang):
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

    # 파일 경로 필터 입력
    tk.Label(control_bar, text="경로 필터:").pack(side="left", padx=(5, 2))
    path_filter_var = tk.StringVar()
    path_filter_entry = tk.Entry(control_bar, textvariable=path_filter_var, width=30)
    path_filter_entry.pack(side="left", padx=(0, 5))
    path_filter_entry.bind("<KeyRelease>", lambda e: _apply_path_filter())

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

        # 실시간(진행 중) 선택
        if source_type == "live":
            groups = get_duplicate_groups()
            if groups:
                _populate_tree(groups, live_label=True)
                is_live_mode["value"] = True
            return

        # JSON 파일 선택
        if source_type == "json":
            from results import _load_groups_from_json, duplicate_results_json_path
            json_path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)
            groups = _load_groups_from_json(json_path, method, hash_size, aspect_ratio_tol, tolerance_rate)
            if groups:
                _populate_tree(groups, live_label=False)
                is_live_mode["value"] = False
            return

        # DB 테이블 선택
        if source_type == "db":
            from comparator import load_duplicate_results_from_db
            groups = load_duplicate_results_from_db(method, hash_size)
            if groups:
                _populate_tree(groups, live_label=False)
                is_live_mode["value"] = False

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

    # ============================================================
    # 결과창 내부 복사본 (원본과 격리)
    # ============================================================
    # saved_groups: 결과창에서 작업하는 복사본 (원본에 영향 없음)
    # deleted_paths: 결과창에서 삭제/제거된 파일 경로 (창 닫기 시 원본에 반영)
    saved_groups = []
    deleted_paths = set()  # 실제 파일 삭제 + 목록 제거된 경로
    is_live_mode = {"value": False}  # 실시간 모드 여부

    def is_checked(item_id):
        return tree.set(item_id, "checked") == "☑"

    def set_checked(item_id, checked):
        tree.set(item_id, "checked", "☑" if checked else "☐")

    def toggle_checked(item_id):
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
        method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
        return duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)

    def save_duplicate_groups_json(groups):
        """복사본을 JSON 파일로 저장"""
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

    def _populate_tree(groups, live_label=False):
        """트리를 groups 데이터로 채우고 saved_groups 동기화"""
        nonlocal saved_groups
        # 깊은 복사본으로 작업 (원본과 격리)
        saved_groups = [list(g) for g in groups] if groups else []
        tree.delete(*tree.get_children())
        first_item = None
        label_suffix = " (실시간)" if live_label else ""
        for gi, group in enumerate(saved_groups, start=1):
            parent_id = tree.insert("", "end", text=f"Group {gi}{label_suffix}", values=("☐", len(group), ""), open=True, tags=("group", gi - 1))
            for file_path in group:
                child_id = tree.insert(parent_id, "end", text=os.path.basename(file_path), values=("☐", "", file_path), tags=("item",), open=False)
                if first_item is None:
                    first_item = child_id
        if saved_groups:
            display_preview_for_group(saved_groups[0])
            if first_item is not None:
                tree.selection_set(first_item)
                tree.focus(first_item)
        # 필터 적용 (이미 입력된 필터가 있으면)
        _apply_path_filter()
        # 상태표시줄 갱신
        update_status_bar()

    def _apply_path_filter():
        """파일 경로 필터 적용 (트리 항목 표시/숨김)"""
        keyword = path_filter_var.get().strip().lower()
        for group_id in tree.get_children():
            group_visible = False
            for child_id in tree.get_children(group_id):
                path = tree.set(child_id, "path").lower()
                if keyword and keyword not in path:
                    tree.detach(child_id)
                else:
                    tree.reattach(child_id, group_id, 0)
                    group_visible = True
            # 그룹에 표시할 항목이 없으면 그룹도 숨김
            if not group_visible:
                tree.detach(group_id)
            else:
                tree.reattach(group_id, "", 0)

    def load_results():
        """저장된 결과를 로드하여 복사본으로 작업"""
        method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
        groups = load_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
        if groups is None:
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("no_saved_results", "저장된 중복 검색 결과가 없습니다."))
            return []
        is_live_mode["value"] = False
        _populate_tree(groups, live_label=False)
        return saved_groups

    def display_preview_for_group(group):
        nonlocal preview_images
        for child in preview_inner.winfo_children():
            child.destroy()
        preview_images = []
        preview_canvas.yview_moveto(0)  # 스크롤 위치 초기화

        if not group:
            return

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

            if not os.path.exists(file_path):
                label = tk.Label(frame, text=lang["ui"].get("missing_file", "파일을 찾을 수 없습니다."), fg="red")
                label.pack(fill="x")
                continue

            try:
                img = Image.open(file_path)
                img.thumbnail((300, 300))
                photo = ImageTk.PhotoImage(img)
                preview_images.append(photo)
                img_label = tk.Label(frame, image=photo)
                img_label.image = photo
                img_label.pack()
                img_label.bind("<Double-1>", lambda event, path=file_path: open_file(path))
                img_label.bind("<Button-3>", lambda event, path=file_path: open_folder_for_file(path))
            except Exception:
                label = tk.Label(frame, text=lang["ui"].get("preview_error", "미리보기를 로드할 수 없습니다."), fg="red")
                label.pack(fill="x")

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
            # DB 캐시에서도 존재하지 않는 파일 제거
            method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
            remove_missing_files_from_cache(method, hash_size, missing_paths)
            save_duplicate_groups_json(new_groups)
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("removed_missing", "없는 파일 목록이 제거되었습니다."))
            _populate_tree(new_groups, live_label=False)

    def remove_selected_items():
        """선택된 항목을 복사본에서 제거 (실제 파일 삭제 아님)"""
        checked_groups, checked_files = get_checked_items()
        if not checked_groups and not checked_files:
            return
        changed = False
        current_groups = [list(g) for g in saved_groups]
        # 삭제할 그룹 인덱스 저장 (표시상의 인덱스 계산 위해)
        for group_index in sorted(checked_groups, reverse=True):
            if 0 <= group_index < len(current_groups):
                for p in current_groups[group_index]:
                    deleted_paths.add(p)
                current_groups[group_index] = []
                changed = True
        for file_path, group_id in checked_files:
            group_index = _get_group_index(group_id)
            if 0 <= group_index < len(current_groups):
                if file_path in current_groups[group_index]:
                    current_groups[group_index].remove(file_path)
                    deleted_paths.add(file_path)
                    changed = True
        new_groups = [g for g in current_groups if len(g) > 1]
        if changed:
            save_duplicate_groups_json(new_groups)
            # 트리 재구성 시 미리보기도 첫 그룹으로 갱신되므로
            # 갱신된 목록과 미리보기를 동기화하기 위해 _populate_tree 호출
            _populate_tree(new_groups, live_label=False)
            # _populate_tree는 display_preview_for_group(saved_groups[0])으로 미리보기를 갱신함
            # (saved_groups가 new_groups로 업데이트되었으므로 첫 그룹 표시)
            if new_groups:
                display_preview_for_group(new_groups[0])
            else:
                display_preview_for_group([])

    def delete_selected_files():
        """선택된 파일을 실제로 삭제하고 복사본에서도 제거"""
        checked_groups, checked_files = get_checked_items()
        if not checked_groups and not checked_files:
            return
        confirm_msg = lang["ui"].get(
            "delete_files_confirm",
            "선택한 파일을 실제로 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.",
        )
        if not messagebox.askyesno(lang["ui"].get("confirm", "확인"), confirm_msg, default=messagebox.NO):
            return
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
            )

    def invert_selection():
        for group_id in tree.get_children():
            current_group = is_checked(group_id)
            set_checked(group_id, not current_group)
            for child_id in tree.get_children(group_id):
                set_checked(child_id, not is_checked(child_id))
        update_status_bar()

    def select_all():
        """모든 그룹/항목 체크"""
        for group_id in tree.get_children():
            set_checked(group_id, True)
            for child_id in tree.get_children(group_id):
                set_checked(child_id, True)
        update_status_bar()

    def deselect_all():
        """모든 그룹/항목 체크 해제"""
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
        # 1. 삭제/제거된 항목을 원본에 반영
        apply_changes_on_close()
        # 2. deleted_paths 초기화 (이미 반영했으므로)
        deleted_paths.clear()
        # 3. 원본에서 다시 복사본 가져오기
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
    # ============================================================
    live_refresh_enabled = {"value": True}
    live_refresh_after_id = {"id": None}
    last_group_count = {"value": 0}  # 마지막으로 확인한 그룹 수

    def refresh_live_groups():
        """진행 중인 비교의 중복 그룹을 실시간으로 트리에 반영 (백그라운드)"""
        if not live_refresh_enabled["value"]:
            return

        # 백그라운드 스레드에서 get_duplicate_groups() 실행 (UI 블로킹 방지)
        def fetch_groups():
            if not live_refresh_enabled["value"]:
                return
            try:
                if not is_stop_requested():
                    groups = get_duplicate_groups()
                    if groups and len(groups) != last_group_count["value"]:
                        # UI 스레드에서 트리 갱신
                        def update_ui():
                            try:
                                is_live_mode["value"] = True
                                _populate_tree(groups, live_label=True)
                                last_group_count["value"] = len(groups)
                            except Exception:
                                pass
                        win.after(0, update_ui)
            except Exception:
                pass

        threading.Thread(target=fetch_groups, daemon=True).start()

        # 다음 폴링 예약 (2초 간격 - UI 부하 감소)
        live_refresh_after_id["id"] = win.after(2000, refresh_live_groups)

    def stop_live_refresh():
        """실시간 갱신 중지"""
        live_refresh_enabled["value"] = False
        if live_refresh_after_id["id"] is not None:
            try:
                win.after_cancel(live_refresh_after_id["id"])
            except Exception:
                pass
            live_refresh_after_id["id"] = None

    def apply_changes_on_close():
        """창 닫기 시 삭제/제거된 파일을 원본 결과에 반영"""
        if deleted_paths:
            try:
                method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options()
                # DB 캐시에서 삭제된 파일 제거
                remove_missing_files_from_cache(method, hash_size, list(deleted_paths))
                # 수정된 그룹을 JSON에 저장
                if saved_groups:
                    save_duplicate_groups_json([list(g) for g in saved_groups if len(g) > 1])
                logger.info(f"[bold cyan][알림] 결과창 변경사항 반영: {len(deleted_paths)}개 파일 제거됨[/bold cyan]")
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

    # 창이 닫힐 때: 실시간 갱신 중지 + 변경사항 원본 반영
    win.bind("<Destroy>", lambda e: (stop_live_refresh(), apply_changes_on_close()))
