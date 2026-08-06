import tkinter as tk
import threading
from tkinterdnd2 import TkinterDnD, DND_FILES

from config import load_config, save_config
from lang import load_lang
from ui_options import *
from ui_settings import show_settings_window
from ui_results import show_duplicate_results_window

from tooltip import add_tooltip
from ui_cache import get_cache_counts, clear_cache, drop_db
from folder_list import (
    create_folder_list, create_count_label,
    add_folder, drop, clear_list, delete_selected, update_count,
    save_folder_list, load_folder_list,
    get_checked_folders,
)
from compare import try_compare, request_stop, reset_stop
from logger import logger


# ============================================================
# 창 중복 생성 방지 (종류별 최대 1개)
# ============================================================
_open_windows = {}  # {창종류: Toplevel 인스턴스}


def show_single_window(window_key, create_fn, root, lang, *args, **kwargs):
    """
    종류별로 창을 최대 1개만 생성.
    이미 열려있으면 해당 창에 포커스를 주고 새로 만들지 않음.
    - window_key: 창 식별자 (예: "options", "results", "settings")
    - create_fn: 창 생성 함수 (root, lang을 인자로 받음)
    - *args, **kwargs: create_fn에 추가로 전달할 인자
    """
    # 이미 열려있는 창이 있으면 포커스만 이동
    existing = _open_windows.get(window_key)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass
        # 창이 닫혔으면 정리
        _open_windows.pop(window_key, None)

    # 새 창 생성
    win = create_fn(root, lang, *args, **kwargs)

    # 창이 닫힐 때 _open_windows에서 제거되도록 설정
    def on_window_close():
        _open_windows.pop(window_key, None)
        try:
            win.destroy()
        except Exception:
            pass

    try:
        win.protocol("WM_DELETE_WINDOW", on_window_close)
    except Exception:
        pass

    _open_windows[window_key] = win
    return win

def main():
    # 설정 및 언어 로드
    config = load_config()
    lang = load_lang(config.get("language", "en"))
    logger.info("[bold green]🚀 이미지 중복 탐색기가 실행되었습니다.[/bold green]")

    root = TkinterDnD.Tk()
    root.title("이미지 중복 탐색기 - 폴더 선택")

    root.rowconfigure(0, weight=0)
    root.rowconfigure(1, weight=1)
    root.rowconfigure(2, weight=0)
    root.columnconfigure(0, weight=1)

    # 최상단 버튼 프레임 (기존 최하단 button_frame을 최상단으로 이동)
    button_frame = tk.Frame(root)
    button_frame.grid(row=0, column=0, columnspan=2, sticky="ew")

    folder_list = create_folder_list(root)
    folder_list.grid(row=1, column=0, columnspan=2, sticky="nsew")

    info_frame = tk.Frame(root)
    info_frame.grid(row=2, column=0, columnspan=2, sticky="w" )

    
    settings_btn = tk.Button(info_frame, text=lang["ui"]["settings_button"],
              command=lambda: show_single_window("settings", show_settings_window, root, lang))
    settings_btn.pack(side="left")
    add_tooltip(settings_btn, lang["ui"].get("tooltip_settings", ""))

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

    clear_cache_btn = tk.Button(info_frame, text="캐시 초기화",
              command=lambda: clear_cache(update_cache_ui))
    clear_cache_btn.pack(side="left")
    add_tooltip(clear_cache_btn, lang["ui"].get("tooltip_cache_clear", ""))

    drop_db_btn = tk.Button(info_frame, text="DB 삭제",
              command=lambda: drop_db(update_cache_ui))
    drop_db_btn.pack(side="left")
    add_tooltip(drop_db_btn, lang["ui"].get("tooltip_cache_drop", ""))

    refresh_cache_btn = tk.Button(info_frame, text="캐시 새로고침",
              command=lambda: update_cache_ui(verbose=True))
    refresh_cache_btn.pack(side="left")
    add_tooltip(refresh_cache_btn, lang["ui"].get("tooltip_cache_refresh", ""))

    update_cache_ui()

    all_apply_var = tk.BooleanVar(value=config.get("apply_depth_all", False))
    last_depth_cache = {"depth": None}

    def save_apply_depth_option():
        config["apply_depth_all"] = all_apply_var.get()
        save_config(config)

    tk.Checkbutton(button_frame, text="깊이 일괄 적용",
                   variable=all_apply_var,
                   command=save_apply_depth_option).pack(side="left")

    add_folder_btn = tk.Button(button_frame, text=lang["ui"]["add_folder"],
              command=lambda: add_folder(root, folder_list, update_count,
                                         all_apply_var, last_depth_cache, lang, count_label))
    add_folder_btn.pack(side="left")
    add_tooltip(add_folder_btn, lang["ui"].get("tooltip_add_folder", ""))

    clear_list_btn = tk.Button(button_frame, text=lang["ui"]["clear_list"],
              command=lambda: clear_list(folder_list, update_count, lang, count_label))
    clear_list_btn.pack(side="left")
    add_tooltip(clear_list_btn, lang["ui"].get("tooltip_clear_list", ""))

    options_btn = tk.Button(button_frame, text=lang["ui"]["options_button"],
                  command=lambda: show_single_window("options", show_options, root, lang))
    options_btn.pack(side="left")
    add_tooltip(options_btn, lang["ui"].get("tooltip_options", ""))

    is_comparing = False
    user_stop_flag = {"requested": False}

    compare_btn = tk.Button(button_frame, text=lang["ui"]["compare_button"])
    compare_btn.pack(side="left")
    add_tooltip(compare_btn, lang["ui"].get("tooltip_compare", ""))

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
    add_tooltip(stop_btn, lang["ui"].get("tooltip_stop", ""))

    results_btn = tk.Button(button_frame, text=lang["ui"].get("duplicate_results_window", "중복 결과"),
              command=lambda: show_single_window("results", show_duplicate_results_window, root, lang, folder_list))
    results_btn.pack(side="left")
    add_tooltip(results_btn, lang["ui"].get("tooltip_duplicate_results", ""))

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
                # 체크된 폴더 수 확인
                checked = get_checked_folders(folder_list)
                if not checked:
                    logger.warning("[bold yellow][알림] 검사할 폴더가 선택되지 않았습니다. 폴더를 체크해주세요.[/bold yellow]")
                    return
                result = try_compare(folder_list)
                total_duplicates = result.get("total_duplicates", 0) if isinstance(result, dict) else (result or 0)
                has_remaining = bool(result.get("has_remaining", False)) if isinstance(result, dict) else False
                # 비교 완료 시 결과창 자동 오픈 제거 (사용자가 직접 열도록)
                # 중복이 발견된 경우 로그로만 알림
                if total_duplicates:
                    logger.info(f"[bold green][알림] 중복 {total_duplicates}건 발견. '중복 결과' 버튼으로 확인하세요.[/bold green]")
            except Exception as e:
                logger.error(f"[bold red][오류][/bold red] 비교 도중 예외가 발생했습니다: {e}")
            finally:
                is_comparing = False
                root.after(0, lambda: compare_btn.config(state="normal"))
                root.after(0, lambda: stop_btn.config(state="disabled"))
                root.after(0, update_cache_ui)
                if auto_retry_var.get() and has_remaining and not user_stop_flag["requested"]:
                    logger.info("[bold cyan][알림] 비교할 건수가 남아 자동 재시도합니다.[/bold cyan]")
                    # 결과창이 열려 있으면 '자동 재갱신' 체크박스가 켜져 있을 때 자동 갱신
                    results_win = _open_windows.get("results")
                    if results_win is not None:
                        try:
                            if hasattr(results_win, "notify_compare_retry"):
                                results_win.notify_compare_retry()
                        except Exception:
                            pass
                    root.after(0, start_compare_thread)

        threading.Thread(target=worker, daemon=True).start()

    compare_btn.config(command=start_compare_thread)

    folder_list._tree.drop_target_register(DND_FILES)
    folder_list._tree.dnd_bind('<<Drop>>',
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
