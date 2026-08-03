import tkinter as tk
import threading
from tkinterdnd2 import TkinterDnD, DND_FILES

from config import load_config, save_config
from lang import load_lang
from ui_options import *
from ui_settings import show_settings_window
from ui_results import show_duplicate_results_window

from ui_cache import get_cache_counts, clear_cache, drop_db
from folder_list import (
    create_folder_list, create_count_label,
    add_folder, drop, clear_list, delete_selected, update_count,
    save_folder_list, load_folder_list
)
from compare import try_compare, request_stop, reset_stop
from logger import logger

def main():
    # 설정 및 언어 로드
    config = load_config()
    lang = load_lang(config.get("language", "en"))
    logger.info("[bold green]🚀 이미지 중복 탐색기가 실행되었습니다.[/bold green]")

    root = TkinterDnD.Tk()
    root.title("이미지 중복 탐색기 - 폴더 선택")

    root.rowconfigure(0, weight=1)
    root.rowconfigure(1, weight=0)
    root.rowconfigure(2, weight=0)
    root.columnconfigure(0, weight=1)

    folder_list = create_folder_list(root)

    scroll_y = tk.Scrollbar(root, orient="vertical")
    scroll_y.grid(row=0, column=1, sticky="ns")

    scroll_x = tk.Scrollbar(root, orient="horizontal")
    scroll_x.grid(row=1, column=0, sticky="ew")

    folder_list.config(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
    folder_list.grid(row=0, column=0, sticky="nsew")

    scroll_y.config(command=folder_list.yview)
    scroll_x.config(command=folder_list.xview)

    info_frame = tk.Frame(root)
    info_frame.grid(row=2, column=0, columnspan=2, sticky="w" )

    
    tk.Button(info_frame, text=lang["ui"]["settings_button"],
              command=lambda: show_settings_window(root, lang)).pack(side="left")

    count_label = create_count_label(info_frame, lang)
    count_label.pack(side="left", padx=(0, 2))

    cache_count_label = tk.Label(info_frame, text="", fg="gray30")
    cache_count_label.pack(side="left", padx=(0, 5))

    def update_cache_ui(verbose=False):
        config = load_config()
        method = config.get("compare_method", "ahash")
        hash_size = int(config.get("hash_size", 8))
        h_cnt, c_cnt, p_cnt, d_cnt = get_cache_counts(method, hash_size)
        cache_count_label.config(text=f" |  캐시({method}, {hash_size}): 해시 {h_cnt:,}개 / 비교 {c_cnt:,}개 / 진행 {p_cnt:,}개 / 중복 {d_cnt:,}개")
        if verbose:
            logger.info(f"[bold cyan][알림] 캐시 건수 새로고침 완료 (알고리즘: {method}, 해시 크기: {hash_size}, 해시: {h_cnt:,}개, 비교: {c_cnt:,}개, 진행: {p_cnt:,}개, 중복: {d_cnt:,}개)[/bold cyan]")

    tk.Button(info_frame, text="캐시 초기화",
              command=lambda: clear_cache(update_cache_ui)).pack(side="left")

    tk.Button(info_frame, text="DB 삭제",
              command=lambda: drop_db(update_cache_ui)).pack(side="left")

    tk.Button(info_frame, text="캐시 새로고침",
              command=lambda: update_cache_ui(verbose=True)).pack(side="left")

    update_cache_ui()

    button_frame = tk.Frame(root)
    button_frame.grid(row=3, column=0, columnspan=2, sticky="ew")

    all_apply_var = tk.BooleanVar(value=config.get("apply_depth_all", False))
    last_depth_cache = {"depth": None}

    def save_apply_depth_option():
        config["apply_depth_all"] = all_apply_var.get()
        save_config(config)

    tk.Checkbutton(button_frame, text="깊이 일괄 적용",
                   variable=all_apply_var,
                   command=save_apply_depth_option).pack(side="left")

    tk.Button(button_frame, text=lang["ui"]["add_folder"],
              command=lambda: add_folder(root, folder_list, update_count,
                                         all_apply_var, last_depth_cache, lang, count_label)).pack(side="left")

    tk.Button(button_frame, text=lang["ui"]["clear_list"],
              command=lambda: clear_list(folder_list, update_count, lang, count_label)).pack(side="left")

    tk.Button(button_frame, text=lang["ui"]["options_button"],
                  command=lambda: show_options(root, lang)).pack(side="left")

    is_comparing = False
    user_stop_flag = {"requested": False}

    compare_btn = tk.Button(button_frame, text=lang["ui"]["compare_button"])
    compare_btn.pack(side="left")

    auto_retry_var = tk.BooleanVar(value=config.get("auto_retry_compare", False))

    def save_auto_retry_option():
        config["auto_retry_compare"] = auto_retry_var.get()
        save_config(config)

    tk.Checkbutton(
        button_frame,
        text=lang["ui"].get("auto_retry_compare", "완료 시 자동 재시도"),
        variable=auto_retry_var,
        command=save_auto_retry_option,
    ).pack(side="left")

    stop_btn = tk.Button(button_frame, text=lang["ui"].get("stop_button", "비교 중단"), state="disabled")
    stop_btn.pack(side="left")

    tk.Button(button_frame, text=lang["ui"].get("duplicate_results_window", "중복 결과"),
              command=lambda: show_duplicate_results_window(root, lang)).pack(side="left")

    def on_stop_click():
        user_stop_flag["requested"] = True
        request_stop()
        logger.warning("[bold yellow][알림] 비교 중단 요청이 접수되었습니다. 현재 진행 중인 루프 종료 후 멈춥니다...[/bold yellow]")
        stop_btn.config(state="disabled")

    stop_btn.config(command=on_stop_click)

    def start_compare_thread():
        nonlocal is_comparing
        if is_comparing:
            logger.warning("[bold yellow][알림] 이미 비교 작업이 진행 중입니다.[/bold yellow]")
            return

        is_comparing = True
        user_stop_flag["requested"] = False
        reset_stop()
        compare_btn.config(state="disabled")
        stop_btn.config(state="normal")

        def worker():
            nonlocal is_comparing
            has_remaining = False
            try:
                result = try_compare(folder_list)
                total_duplicates = result.get("total_duplicates", 0) if isinstance(result, dict) else (result or 0)
                has_remaining = bool(result.get("has_remaining", False)) if isinstance(result, dict) else False
                options = load_config()
                if total_duplicates and options.get("auto_open_duplicate_results", False):
                    root.after(0, lambda: show_duplicate_results_window(root, lang))
            except Exception as e:
                logger.error(f"[bold red][오류][/bold red] 비교 도중 예외가 발생했습니다: {e}")
            finally:
                is_comparing = False
                root.after(0, lambda: compare_btn.config(state="normal"))
                root.after(0, lambda: stop_btn.config(state="disabled"))
                root.after(0, update_cache_ui)
                if auto_retry_var.get() and has_remaining and not user_stop_flag["requested"]:
                    logger.info("[bold cyan][알림] 비교할 건수가 남아 자동 재시도합니다.[/bold cyan]")
                    root.after(0, start_compare_thread)

        threading.Thread(target=worker, daemon=True).start()

    compare_btn.config(command=start_compare_thread)

    folder_list.drop_target_register(DND_FILES)
    folder_list.dnd_bind('<<Drop>>',
        lambda e: drop(e, root, folder_list, update_count,
                       all_apply_var, last_depth_cache, lang, count_label))

    root.bind("<Delete>", lambda e: delete_selected(e, folder_list, update_count, lang, count_label))

    # --- 프로그램 시작 시 폴더 목록 불러오기 ---
    load_folder_list(folder_list, update_count, lang, count_label)

    # --- 프로그램 종료 시 폴더 목록 저장 ---
    def on_close():
        save_folder_list(folder_list)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
