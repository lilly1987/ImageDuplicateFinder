import tkinter as tk
from tkinter import Toplevel, StringVar, OptionMenu
from config import load_config, save_config
from shortcuts import (
    ACTION_DEFS, make_shortcut_capture_entry,
    find_duplicate_shortcuts, apply_shortcuts,
)
from logger import logger


def show_settings_window(root, lang):
    options = load_config()
    win = Toplevel(root)
    win.title(lang["ui"]["settings_title"])
    win.minsize(480, 400)

    # ── 기본 설정 ──
    tk.Label(win, text=lang["ui"]["language"]).grid(row=0, column=0, sticky="w", padx=6, pady=4)
    lang_var = StringVar(value=options.get("language", "en"))
    OptionMenu(win, lang_var, "ko", "en").grid(row=0, column=1, sticky="ew", padx=6, pady=4)

    save_txt_var = tk.BooleanVar(value=options.get("save_txt_results", True))
    tk.Checkbutton(
        win,
        text=lang["ui"].get("save_txt_results", "txt 파일로 결과 저장"),
        variable=save_txt_var,
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6)

    # ── 단축키 설정 ──
    shortcut_frame = tk.LabelFrame(win, text=lang["ui"].get("keyboard_shortcuts", "단축키"), padx=8, pady=8)
    shortcut_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=8, pady=(10, 4))
    win.columnconfigure(0, weight=1)
    win.rowconfigure(2, weight=1)

    # 캔버스 + 스크롤바
    canvas = tk.Canvas(shortcut_frame, highlightthickness=0)
    scrollbar = tk.Scrollbar(shortcut_frame, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas)
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    shortcut_vars = {}

    groups = [
        ("main", lang["ui"].get("shortcuts_group_main", "메인 창")),
        ("results", lang["ui"].get("shortcuts_group_results", "결과창")),
    ]

    row_idx = 0
    for window_type, group_label in groups:
        actions = [(k, v) for k, v in ACTION_DEFS.items() if v["window"] == window_type]
        if not actions:
            continue
        tk.Label(scroll_frame, text=group_label, font=("", 10, "bold"), anchor="w").grid(
            row=row_idx, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        row_idx += 1
        for action_key, info in actions:
            current = options.get("shortcuts", {}).get(action_key, "") or info["default"] or ""
            label_text = lang["ui"].get(info["label_key"], action_key)
            tk.Label(scroll_frame, text=label_text, anchor="w", width=30).grid(
                row=row_idx, column=0, sticky="w", pady=1
            )
            var = tk.StringVar(value=current)
            shortcut_vars[action_key] = var
            entry = make_shortcut_capture_entry(
                scroll_frame, initial=current,
                on_change=lambda s, k=action_key: shortcut_vars[k].set(s),
            )
            entry.grid(row=row_idx, column=1, sticky="w", padx=4)
            default_val = info["default"] or ""
            if default_val:
                def reset_default(a=action_key, d=default_val):
                    shortcut_vars[a].set(d)
                tk.Button(scroll_frame, text="↺", width=2, command=reset_default).grid(
                    row=row_idx, column=2, padx=2
                )
            row_idx += 1

    warning_var = tk.StringVar(value="")
    warning_label = tk.Label(win, textvariable=warning_var, fg="red", wraplength=450)
    warning_label.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8)

    def _check_duplicates(*_args):
        test_config = dict(options)
        test_config["shortcuts"] = {k: v.get() for k, v in shortcut_vars.items()}
        dupes = find_duplicate_shortcuts(test_config)
        if dupes:
            parts = []
            for combo, act_list in dupes.items():
                parts.append(f"{combo}: {', '.join(act_list)}")
            warning_var.set(
                lang["ui"].get("shortcut_duplicate_warning", "중복된 단축키: ") + "; ".join(parts)
            )
        else:
            warning_var.set("")

    for var in shortcut_vars.values():
        var.trace_add("write", _check_duplicates)

    def reset_all_defaults():
        for action_key, info in ACTION_DEFS.items():
            shortcut_vars[action_key].set(info["default"] or "")
        _check_duplicates()

    tk.Button(win, text=lang["ui"].get("reset_shortcuts", "단축키 초기화"), command=reset_all_defaults).grid(
        row=4, column=0, sticky="w", padx=8, pady=(8, 2)
    )

    def save_and_close():
        options["language"] = lang_var.get()
        options["save_txt_results"] = save_txt_var.get()
        shortcuts_dict = {}
        for k, v in shortcut_vars.items():
            # 빈 문자열도 저장 (빈 값 = 해당 단축키 비활성화)
            shortcuts_dict[k] = v.get().strip()
        options["shortcuts"] = shortcuts_dict
        dupes = find_duplicate_shortcuts(options)
        if dupes:
            from tkinter import messagebox
            parts = [f"{c}: {', '.join(a)}" for c, a in dupes.items()]
            messagebox.showwarning(
                lang["ui"].get("shortcut_duplicate_title", "단축키 중복"),
                lang["ui"].get("shortcut_duplicate_warning", "중복된 단축키가 있습니다.") + "\n" + "; ".join(parts),
                parent=win,
            )
            return
        save_config(options)
        try:
            apply_shortcuts(root, options, "main")
        except Exception as e:
            logger.error(f"단축키 적용 실패: {e}")
        win.destroy()

    tk.Button(win, text=lang["ui"]["save"], command=save_and_close).grid(
        row=4, column=1, sticky="e", padx=8, pady=(8, 2)
    )

