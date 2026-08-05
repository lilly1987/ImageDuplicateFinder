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
    filter_entry = tk.Entry(top_bar, textvariable=filter_var, width=40)
    filter_entry.pack(side="left", padx=(0, 5))
    filter_entry.bind("<KeyRelease>", lambda e: _apply_filter(container))

    # ---- 하단: 체크박스 Treeview ----
    tree_frame = tk.Frame(container)
    tree_frame.pack(side="top", fill="both", expand=True)

    tree = ttk.Treeview(tree_frame, columns=("checked", "path"), show="tree headings", selectmode="extended")
    tree.heading("#0", text="")
    tree.heading("checked", text="선택")
    tree.heading("path", text="폴더 경로")
    tree.column("#0", width=40, anchor="center", stretch=False)
    tree.column("checked", width=60, anchor="center", stretch=False)
    tree.column("path", width=600, anchor="w", stretch=True)
    tree.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

    # 체크박스 토글 클릭
    tree.bind("<Button-1>", lambda e: _on_tree_click(tree, e))

    # 컨테이너에 위젯들 저장
    container._tree = tree
    container._filter_var = filter_var
    container._filter_entry = filter_entry
    container._select_all_btn = select_all_btn
    container._deselect_all_btn = deselect_all_btn
    container._invert_btn = invert_btn
    # 필터로 숨겨진(detach된) 항목 ID 추적 (필터 해제 시 복원용)
    container._detached = set()

    return container


def _on_tree_click(tree, event):
    """체크박스 컬럼 클릭 시 토글"""
    item_id = tree.identify_row(event.y)
    col = tree.identify_column(event.x)
    if not item_id or col != "#1":
        return
    current = tree.set(item_id, "checked")
    tree.set(item_id, "checked", "☐" if current == "☑" else "☑")
    return "break"


def _set_all_checked(container, checked):
    """모든 (필터링된) 항목 체크 상태 설정"""
    tree = container._tree
    for item_id in tree.get_children():
        tree.set(item_id, "checked", "☑" if checked else "☐")


def _invert_all_checked(container):
    """모든 (필터링된) 항목 체크 상태 반전"""
    tree = container._tree
    for item_id in tree.get_children():
        current = tree.set(item_id, "checked")
        tree.set(item_id, "checked", "☐" if current == "☑" else "☑")


def _apply_filter(container):
    """
    필터 입력에 따라 목록 표시/숨김.
    - detach된 항목은 container._detached에 보관하여 필터 해제 시 복원.
    - 필터는 검색 목적이므로 항목을 영구 제거하지 않음.
    """
    tree = container._tree
    keyword = container._filter_var.get().strip().lower()

    # 1. 현재 표시 중인 항목 중 필터에 맞지 않는 것 숨기기
    for item_id in tree.get_children():
        path = tree.set(item_id, "path").lower()
        if keyword and keyword not in path:
            tree.detach(item_id)
            container._detached.add(item_id)

    # 2. 필터가 비어있으면 숨겨진 항목 모두 복원
    if not keyword:
        for item_id in list(container._detached):
            try:
                tree.reattach(item_id, "", 0)
            except Exception:
                pass
        container._detached.clear()
    else:
        # 3. 필터가 있으면 숨겨진 항목 중 필터에 맞는 것 복원
        for item_id in list(container._detached):
            try:
                path = tree.set(item_id, "path").lower()
                if keyword in path:
                    tree.reattach(item_id, "", 0)
                    container._detached.discard(item_id)
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
    existing = {tree.set(iid, "path") for iid in tree.get_children()}
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


def clear_list(folder_list, update_count, lang, count_label):
    folder_list._tree.delete(*folder_list._tree.get_children())
    update_count(folder_list, lang, count_label)


def delete_selected(event, folder_list, update_count, lang, count_label):
    tree = folder_list._tree
    selected = tree.selection()
    for iid in selected:
        tree.delete(iid)
    update_count(folder_list, lang, count_label)


def update_count(folder_list, lang, count_label=None):
    """표시 중인 전체 항목 수 갱신"""
    tree = folder_list._tree
    total = len(tree.get_children())
    checked = sum(1 for iid in tree.get_children() if tree.set(iid, "checked") == "☑")
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
    """표시된 모든 폴더 경로 목록 반환"""
    tree = folder_list._tree
    return [tree.set(iid, "path") for iid in tree.get_children()]


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