import tkinter as tk
from tkinter import Toplevel, StringVar, OptionMenu
from config import load_config, save_config

def show_settings_window(root, lang):
    options = load_config()
    win = Toplevel(root)
    win.title(lang["ui"]["settings_title"])

    tk.Label(win, text=lang["ui"]["language"]).grid(row=0, column=0, sticky="w")
    lang_var = StringVar(value=options.get("language", "en"))
    OptionMenu(win, lang_var, "ko", "en").grid(row=0, column=1, sticky="ew")

    # txt 결과 저장 여부 체크박스 (언어 설정 밑)
    save_txt_var = tk.BooleanVar(value=options.get("save_txt_results", True))
    tk.Checkbutton(
        win,
        text=lang["ui"].get("save_txt_results", "txt 파일로 결과 저장"),
        variable=save_txt_var,
    ).grid(row=1, column=0, columnspan=2, sticky="w")

    def save_and_close():
        options["language"] = lang_var.get()
        options["save_txt_results"] = save_txt_var.get()
        save_config(options)
        win.destroy()

    tk.Button(win, text=lang["ui"]["save"], command=save_and_close).grid(row=2, column=0, columnspan=2)
