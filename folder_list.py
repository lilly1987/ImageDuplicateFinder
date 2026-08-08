"""
폴더 목록 모듈.

체크박스(Treeview) 기반 폴더 목록, 필터 입력, 전체선택/해제/반전,
검색 시 체크된 폴더만 반환.
"""

import os
import tkinter as tk
import yaml
from tkinter import ttk, filedialog, simpledialog

LIST_FILE = "list.yml"


def create_folder_list(root):
    """
    폴더 목록 컨테이너 생성.
    - 상단: 필터 입력 + 전체선택/해제/반전 버튼
    - 하단: 체크박스가 있는 Treeview
    반환: 컨테이너 프레임 (내부에 treeview, 필터, 선택 버튼 포함)
    """
    container = tk.Frame(root)
    # grid 배치는 run.py에서 명시적으로 처리 (버튼을 최상단에 배치하기 위함)

    # ---- 상단: 선택 버튼 + 필터 ----
    top_bar = tk.Frame(container)
    top_bar.pack(side="top", fill="x")

    select_all_btn = tk.Button(top_bar, text="전체선택", command=lambda: _set_all_checked(container, True))
    select_all_btn.pack(side="left", padx=(2, 2))

    deselect_all_btn = tk.Button(top_bar, text="전체해제", command=lambda: _set_all_checked(container, False))
    deselect_all_btn.pack(side="left", padx=(2, 2))

    invert_btn = tk.Button(top_bar, text="반전", command=lambda: _invert_all_checked(container))
    invert_btn.pack(side="left", padx=(2, 2))

    tk.Label(top_bar, text="필터:").pack(side="left", padx=(10, 2))
    filter_var = tk.StringVar()
    filter_entry = tk.Entry(top_bar, textvariable=filter_var, width=25)
    filter_entry.pack(side="left", padx=(0, 5))
    filter_entry.bind("<KeyRelease>", lambda e: _apply_filter(container))

    # 라디오 버튼: 전체보기 / 체크만 보기 / 체크해제만 보기
    view_mode_var = tk.StringVar(value="all")
    rb_all = tk.Radiobutton(top_bar, text="전체보기", variable=view_mode_var, value="all", command=lambda: _apply_filter(container))
    rb_all.pack(side="left", padx=(5, 2))
    rb_checked = tk.Radiobutton(top_bar, text="체크만 보기", variable=view_mode_var, value="checked_only", command=lambda: _apply_filter(container))
    rb_checked.pack(side="left", padx=(2, 2))
    rb_unchecked = tk.Radiobutton(top_bar, text="체크해제만 보기", variable=view_mode_var, value="unchecked_only", command=lambda: _apply_filter(container))
    rb_unchecked.pack(side="left", padx=(2, 2))

    # ---- 하단: 체크박스 Treeview ----
    tree_frame = tk.Frame(container)
    tree_frame.pack(side="top", fill="both", expand=True)
    tree_frame.grid_rowconfigure(0, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)

    tree = ttk.Treeview(tree_frame, columns=("checked", "path"), show="tree headings", selectmode="extended")
    tree.heading("#0", text="")
    tree.heading("checked", text="선택")
    tree.heading("path", text="폴더 경로 ▽", command=lambda: _sort_by_path(container))
    tree.column("#0", width=40, anchor="center", stretch=False)
    tree.column("checked", width=60, anchor="center", stretch=False)
    tree.column("path", width=1500, minwidth=600, anchor="w", stretch=False)
    tree.grid(row=0, column=0, sticky="nsew")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.grid(row=0, column=1, sticky="ns")

    h_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(xscrollcommand=h_scrollbar.set)
    h_scrollbar.grid(row=1, column=0, sticky="ew")

    # 체크박스 토글 클릭
    tree.bind("<Button-1>", lambda e: _on_tree_click(tree, e))

    # Ctrl+C: 선택된 항목(체크 아님)들의 경로 복사
    tree.bind("<Control-c>", lambda e: _copy_selected_paths(tree))

    # 컨테이너에 위젯들 저장
    container._tree = tree
    container._filter_var = filter_var
    container._filter_entry = filter_entry
    container._view_mode_var = view_mode_var
    container._select_all_btn = select_all_btn
    container._deselect_all_btn = deselect_all_btn
    container._invert_btn = invert_btn
    # 필터로 숨겨진(detach된) 항목 ID 추적 (필터 해제 시 복원용)
    container._detached = set()
    # 정렬 상태: True=오름차순, False=내림차순
    container._sort_ascending = True

    return container


def _copy_selected_paths(tree):
    """선택된(하이라이트된) 항목들의 경로를 클립보드에 복사 (Ctrl+C)"""
    selected = tree.selection()
    if not selected:
        return None
    paths = []
    for iid in selected:
        p = tree.set(iid, "path")
        if p:
            paths.append(p)
    if paths:
        top = tree.winfo_toplevel()
        top.clipboard_clear()
        top.clipboard_append(os.linesep.join(paths))
    return "break"


def _on_tree_click(tree, event):
    """체크박스 컬럼 클릭 시 토글"""
    item_id = tree.identify_row(event.y)
    col = tree.identify_column(event.x)
    if not item_id or col != "#1":
        return
    current = tree.set(item_id, "checked")
    tree.set(item_id, "checked", "☐" if current == "☑" else "☑")
    # 트리 부모인 container를 찾아 필터 재적용
    container = tree.master.master
    if hasattr(container, "_view_mode_var"):
        _apply_filter(container)
    return "break"


def _set_all_checked(container, checked):
    """모든 항목 체크 상태 설정 (필터로 숨겨진 detach 항목 포함)"""
    tree = container._tree
    all_ids = list(tree.get_children()) + list(container._detached)
    for item_id in all_ids:
        tree.set(item_id, "checked", "☑" if checked else "☐")
    _apply_filter(container)


def _invert_all_checked(container):
    """모든 항목 체크 상태 반전 (필터로 숨겨진 detach 항목 포함)"""
    tree = container._tree
    all_ids = list(tree.get_children()) + list(container._detached)
    for item_id in all_ids:
        current = tree.set(item_id, "checked")
        tree.set(item_id, "checked", "☐" if current == "☑" else "☑")
    _apply_filter(container)


def _sort_by_path(container):
    """폴더 경로 컬럼 헤더 클릭 시 정렬/역정렬 토글"""
    tree = container._tree
    ascending = container._sort_ascending

    # 표시 중인 항목 + 필터로 숨겨진(detach된) 항목 모두 수집
    all_ids = list(tree.get_children()) + list(container._detached)
    # (경로, 항목ID) 쌍으로 정렬
    items = [(tree.set(iid, "path"), iid) for iid in all_ids]
    items.sort(key=lambda x: x[0].lower(), reverse=not ascending)

    # detach된 항목을 먼저 복원 (정렬 순서대로 재배치하기 위해)
    for iid in list(container._detached):
        tree.reattach(iid, "", "end")
    container._detached.clear()

    # 정렬 순서에 따라 트리에 재배치
    for index, (path, iid) in enumerate(items):
        tree.move(iid, "", index)

    # 정렬 방향 반전 + 헤더 텍스트에 화살표 표시
    container._sort_ascending = not ascending
    arrow = "△" if ascending else "▽"
    tree.heading("path", text=f"폴더 경로 {arrow}")

    # 필터 재적용 (정렬 후 필터 조건에 맞지 않는 항목 다시 숨김)
    _apply_filter(container)


def _apply_filter(container):
    """
    필터 입력 및 체크상태 라디오버튼(전체보기, 체크만보기, 체크해제만보기)에 따라 목록 표시/숨김.
    - detach된 항목은 container._detached에 보관하여 필터 해제 시 복원.
    """
    tree = container._tree
    keyword = container._filter_var.get().strip().lower()
    view_mode = container._view_mode_var.get() if hasattr(container, "_view_mode_var") else "all"

    all_ids = set(tree.get_children()) | set(container._detached)

    for item_id in all_ids:
        try:
            path = tree.set(item_id, "path").lower()
            is_chk = tree.set(item_id, "checked") == "☑"

            # 1. 키워드 필터
            match_keyword = not keyword or (keyword in path)

            # 2. 라디오버튼 보기 모드 필터
            match_checked = True
            if view_mode == "checked_only":
                match_checked = is_chk
            elif view_mode == "unchecked_only":
                match_checked = not is_chk

            should_show = match_keyword and match_checked

            if should_show:
                if item_id in container._detached:
                    tree.reattach(item_id, "", 0)
                    container._detached.discard(item_id)
            else:
                if item_id not in container._detached:
                    tree.detach(item_id)
                    container._detached.add(item_id)
        except Exception:
            pass


def create_count_label(root, lang):
    """폴더 개수 표시 라벨 생성"""
    return tk.Label(root, text=f"{lang['ui'].get('total','Total')} 0")


def ask_depth(initial=0):
    """깊이 입력 대화창 (기본값 0)"""
    return simpledialog.askinteger("폴더 깊이", "몇 번째 깊이까지 탐색할까요?",
                                   minvalue=0, maxvalue=10, initialvalue=initial)


def add_folder(root, folder_list, update_count, all_apply_var, last_depth_cache, lang, count_label):
    folder = filedialog.askdirectory()
    if folder:
        if all_apply_var.get() and last_depth_cache["depth"] is not None:
            depth = last_depth_cache["depth"]
        else:
            depth = ask_depth(initial=0)
            last_depth_cache["depth"] = depth
        if depth is not None:
            add_with_depth(folder, depth, folder_list, update_count, lang, count_label)


def drop(event, root, folder_list, update_count, all_apply_var, last_depth_cache, lang, count_label):
    paths = root.tk.splitlist(event.data)
    depth = None
    if all_apply_var.get():
        if last_depth_cache["depth"] is None:
            last_depth_cache["depth"] = ask_depth(initial=0)
        depth = last_depth_cache["depth"]

    for path in paths:
        if not all_apply_var.get():
            depth = ask_depth(initial=0)
        if depth is not None:
            add_with_depth(path, depth, folder_list, update_count, lang, count_label)


def add_with_depth(base_folder, max_depth, folder_list, update_count, lang, count_label):
    """폴더 목록에 항목 추가 (체크 상태로)"""
    tree = folder_list._tree
    # 표시 중인 항목 + 필터로 숨겨진(detach된) 항목 모두 포함하여 중복 판단
    all_ids = list(tree.get_children()) + list(getattr(folder_list, "_detached", set()))
    existing = {tree.set(iid, "path") for iid in all_ids}
    changed = False
    for root_dir, dirs, files in os.walk(base_folder):
        rel_path = os.path.relpath(root_dir, base_folder)
        current_depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
        if current_depth > max_depth:
            dirs[:] = []
            continue
        if current_depth == max_depth:
            if root_dir not in existing:
                tree.insert("", "end", values=("☑", root_dir))
                existing.add(root_dir)
                changed = True
    if changed:
        update_count(folder_list, lang, count_label)
        # 새로 추가된 항목이 필터(키워드/체크 보기 모드)에 맞게 표시되도록 필터 재적용
        _apply_filter(folder_list)


def clear_list(folder_list, update_count, lang, count_label):
    tree = folder_list._tree
    # 표시 중인 항목 + 필터로 숨겨진(detach된) 항목 모두 삭제
    all_ids = list(tree.get_children()) + list(getattr(folder_list, "_detached", set()))
    for iid in all_ids:
        try:
            tree.delete(iid)
        except Exception:
            pass
    folder_list._detached.clear()
    update_count(folder_list, lang, count_label)


def delete_selected(event, folder_list, update_count, lang, count_label):
    tree = folder_list._tree
    selected = tree.selection()
    for iid in selected:
        try:
            tree.delete(iid)
        except Exception:
            pass
        # 삭제된 항목이 detach 목록에 있으면 정리
        folder_list._detached.discard(iid)
    update_count(folder_list, lang, count_label)


def update_count(folder_list, lang, count_label=None):
    """전체 항목 수 갱신 (필터로 숨겨진 detach 항목 포함)"""
    tree = folder_list._tree
    all_ids = list(tree.get_children()) + list(getattr(folder_list, "_detached", set()))
    total = len(all_ids)
    checked = sum(1 for iid in all_ids if tree.set(iid, "checked") == "☑")
    text = f"{lang['ui'].get('total','Total')} {total} (선택 {checked})"
    if count_label:
        count_label.config(text=text)


def get_checked_folders(folder_list):
    """검사 대상(체크된) 폴더 경로 목록 반환 (detach된 항목 포함)"""
    tree = folder_list._tree
    # 표시 중인 항목 + 필터로 숨겨진(detach된) 항목 모두 포함
    all_ids = list(tree.get_children()) + list(folder_list._detached)
    return [tree.set(iid, "path") for iid in all_ids if tree.set(iid, "checked") == "☑"]


def get_all_folders(folder_list):
    """모든 폴더 경로 목록 반환 (필터로 숨겨진 detach 항목 포함)"""
    tree = folder_list._tree
    all_ids = list(tree.get_children()) + list(getattr(folder_list, "_detached", set()))
    return [tree.set(iid, "path") for iid in all_ids]


def get_filtered_checked_folders(folder_list):
    """필터를 적용한 후 체크된 폴더 경로 목록 반환"""
    tree = folder_list._tree
    keyword = folder_list._filter_var.get().strip().lower()
    result = []
    for iid in tree.get_children():
        path = tree.set(iid, "path")
        if tree.set(iid, "checked") == "☑":
            if not keyword or keyword in path.lower():
                result.append(path)
    return result


# --- 폴더 목록 저장/불러오기 (체크 상태 포함) ---
def save_folder_list(folder_list):
    tree = folder_list._tree
    entries = []
    # 표시 중인 항목 + 필터로 숨겨진(detach된) 항목 모두 저장
    all_ids = list(tree.get_children()) + list(folder_list._detached)
    for iid in all_ids:
        checked = tree.set(iid, "checked") == "☑"
        path = tree.set(iid, "path")
        entries.append({"checked": checked, "path": path})
    with open(LIST_FILE, "w", encoding="utf-8") as f:
        yaml.dump(entries, f, allow_unicode=True)


def load_folder_list(folder_list, update_count, lang, count_label):
    tree = folder_list._tree
    if os.path.exists(LIST_FILE):
        with open(LIST_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
        for entry in data:
            if isinstance(entry, dict):
                checked = entry.get("checked", True)
                path = entry.get("path", "")
                tree.insert("", "end", values=("☑" if checked else "☐", path))
            else:
                # 이전 버전 호환 (문자열)
                tree.insert("", "end", values=("☑", entry))
        update_count(folder_list, lang, count_label)