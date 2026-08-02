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

    def save_and_close():
        options["language"] = lang_var.get()
        save_config(options)
        win.destroy()

    tk.Button(win, text=lang["ui"]["save"], command=save_and_close).grid(row=1, column=0, columnspan=2)
