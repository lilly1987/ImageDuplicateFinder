import os
import tkinter as tk
import yaml
from tkinter import filedialog, simpledialog

LIST_FILE = "list.yml"

def create_folder_list(root):
    """TkinterDnD.Tk() root에 연결된 Listbox 생성"""
    return tk.Listbox(root, width=80, height=15, selectmode="extended")

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
    existing = folder_list.get(0, tk.END)
    for root_dir, dirs, files in os.walk(base_folder):
        rel_path = os.path.relpath(root_dir, base_folder)
        current_depth = 0 if rel_path == "." else rel_path.count(os.sep) + 1
        if current_depth > max_depth:
            dirs[:] = []
            continue
        if current_depth == max_depth:
            entry = f"{current_depth}: {root_dir}"
            if entry not in existing:
                folder_list.insert(tk.END, entry)
    update_count(folder_list, lang, count_label)

def clear_list(folder_list, update_count, lang, count_label):
    folder_list.delete(0, tk.END)
    update_count(folder_list, lang, count_label)

def delete_selected(event, folder_list, update_count, lang, count_label):
    selected = folder_list.curselection()
    for i in reversed(selected):
        folder_list.delete(i)
    update_count(folder_list, lang, count_label)

def update_count(folder_list, lang, count_label=None):
    text = f"{lang['ui'].get('total','Total')} {folder_list.size()}"
    if count_label:
        count_label.config(text=text)

# --- 폴더 목록 저장/불러오기 ---
def save_folder_list(folder_list):
    entries = list(folder_list.get(0, tk.END))
    with open(LIST_FILE, "w", encoding="utf-8") as f:
        yaml.dump(entries, f, allow_unicode=True)

def load_folder_list(folder_list, update_count, lang, count_label):
    if os.path.exists(LIST_FILE):
        with open(LIST_FILE, "r", encoding="utf-8") as f:
            entries = yaml.safe_load(f) or []
        for entry in entries:
            folder_list.insert(tk.END, entry)
        update_count(folder_list, lang, count_label)
