import os
import json
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from PIL import Image, ImageTk
from compare import load_duplicate_results_json


def show_duplicate_results_window(root, lang):
    win = tk.Toplevel(root)
    win.title(lang["ui"].get("duplicate_results_window", "중복 결과"))
    win.geometry("1000x600")

    button_bar = tk.Frame(win)
    button_bar.pack(side="top", fill="x")

    main_frame = tk.Frame(win)
    main_frame.pack(fill="both", expand=True)
    main_frame.grid_rowconfigure(0, weight=1)
    main_frame.grid_columnconfigure(0, weight=1)
    main_frame.grid_columnconfigure(1, weight=2)

    tree_frame = tk.Frame(main_frame)
    tree_frame.grid(row=0, column=0, sticky="nsew")

    detail_frame = tk.Frame(main_frame)
    detail_frame.grid(row=0, column=1, sticky="nsew")

    tree = ttk.Treeview(tree_frame, columns=("checked", "count", "path"), show="tree headings")
    tree.heading("#0", text=lang["ui"].get("group_name", "그룹"))
    tree.heading("checked", text=lang["ui"].get("check", "선택"))
    tree.heading("count", text=lang["ui"].get("group_count", "항목 수"))
    tree.heading("path", text=lang["ui"].get("file_path", "파일 경로"))
    tree.column("#0", width=220, anchor="w", stretch=True)
    tree.column("checked", width=60, anchor="center", stretch=False)
    tree.column("count", width=80, anchor="center", stretch=False)
    tree.column("path", width=420, anchor="w", stretch=True)
    tree.pack(fill="both", expand=True, side="left")

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")

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

    saved_groups = []

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

    def get_checked_items():
        checked_groups = set()
        checked_files = []
        for group_id in tree.get_children():
            if is_checked(group_id):
                checked_groups.add(tree.index(group_id))
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

    def preview_open_file(event, path):
        open_file(path)

    def preview_open_folder(event, path):
        open_folder_for_file(path)

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

    def save_duplicate_groups_json(groups):
        data = {"saved_at": datetime.now().isoformat(), "groups": groups}
        with open("duplicate_results.json", "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def load_results():
        nonlocal saved_groups
        groups = load_duplicate_results_json()
        if groups is None:
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("no_saved_results", "저장된 중복 검색 결과가 없습니다."))
            return []
        saved_groups = groups
        tree.delete(*tree.get_children())
        first_item = None
        for gi, group in enumerate(groups, start=1):
            parent_id = tree.insert("", "end", text=f"Group {gi}", values=("☐", len(group), ""), open=True)
            for file_path in group:
                child_id = tree.insert(parent_id, "end", text=os.path.basename(file_path), values=("☐", "", file_path), tags=("item",), open=False)
                if first_item is None:
                    first_item = child_id
        if saved_groups:
            display_preview_for_group(saved_groups[0])
            if first_item is not None:
                tree.selection_set(first_item)
                tree.focus(first_item)
        return groups

    def display_preview_for_group(group):
        nonlocal preview_images
        for child in preview_inner.winfo_children():
            child.destroy()
        preview_images = []

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
        selected = tree.selection()
        if not selected:
            return
        item_id = selected[0]
        parent_id = tree.parent(item_id)
        if parent_id:
            group_index = tree.index(parent_id)
            display_preview_for_group(saved_groups[group_index])
        else:
            group_index = tree.index(item_id)
            if saved_groups and 0 <= group_index < len(saved_groups):
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
        if not saved_groups:
            return
        changed = False
        new_groups = []
        for group in saved_groups:
            remaining = [p for p in group if os.path.exists(p)]
            if len(remaining) > 1:
                new_groups.append(remaining)
            else:
                changed = True
        if changed:
            save_duplicate_groups_json(new_groups)
            messagebox.showinfo(lang["ui"].get("info", "정보"), lang["ui"].get("removed_missing", "없는 파일 목록이 제거되었습니다."))
            load_results()

    def remove_selected_items():
        checked_groups, checked_files = get_checked_items()
        if not checked_groups and not checked_files:
            return
        changed = False
        current_groups = [list(g) for g in saved_groups]
        for group_index in sorted(checked_groups, reverse=True):
            if 0 <= group_index < len(current_groups):
                current_groups[group_index] = []
                changed = True
        for file_path, group_id in checked_files:
            group_index = tree.index(group_id)
            if 0 <= group_index < len(current_groups):
                if file_path in current_groups[group_index]:
                    current_groups[group_index].remove(file_path)
                    changed = True
        new_groups = [g for g in current_groups if len(g) > 1]
        if changed:
            save_duplicate_groups_json(new_groups)
            load_results()

    def delete_selected_files():
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

    tk.Button(button_bar, text=lang["ui"].get("remove_missing", "없는파일 목록에서 제거"), command=remove_missing_files).pack(side="left")
    tk.Button(button_bar, text=lang["ui"].get("remove_selected", "체크한 그룹/항목 목록에서 제거"), command=remove_selected_items).pack(side="left")
    tk.Button(button_bar, text=lang["ui"].get("delete_selected_items", "선택 항목 실제 파일 삭제"), command=delete_selected_files).pack(side="left")
    tk.Button(button_bar, text=lang["ui"].get("invert_selection", "선택 항목 반전"), command=invert_selection).pack(side="left")

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

    load_results()
