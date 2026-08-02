import tkinter as tk
from config import load_config, save_config

def show_options(root, lang):
    win = tk.Toplevel(root)
    win.title(lang["ui"].get("options", "검색/비교 옵션"))

    config = load_config()

    # --- 검색 모드 ---
    tk.Label(win, text=lang["ui"].get("search_mode", "검색 모드")).pack(anchor="w")
    mode_var = tk.StringVar(value=config.get("search_mode", "all_folders"))
    modes = [
        (lang["ui"].get("all_folders", "모든 폴더내 모든 파일 비교"), "all_folders"),
        (lang["ui"].get("per_folder", "각 목록별 폴더내 파일 비교"), "per_folder"),
        (lang["ui"].get("cross_folder", "각 목록의 폴더는 다른 목록 폴더와 비교"), "cross_folder"),
    ]
    for text, val in modes:
        tk.Radiobutton(win, text=text, variable=mode_var, value=val).pack(anchor="w")

    # --- 하위 폴더 옵션 ---
    tk.Label(win, text=lang["ui"].get("include_subfolders", "하위 폴더 옵션")).pack(anchor="w")
    sub_var = tk.StringVar(value=config.get("include_subfolders", "include"))
    subs = [
        (lang["ui"].get("include", "하위 폴더 포함"), "include"),
        (lang["ui"].get("exclude", "하위 폴더 제외"), "exclude"),
    ]
    for text, val in subs:
        tk.Radiobutton(win, text=text, variable=sub_var, value=val).pack(anchor="w")

    # --- 비교 알고리즘 ---
    tk.Label(win, text=lang["ui"].get("compare_method", "비교 알고리즘")).pack(anchor="w")
    method_var = tk.StringVar(value=config.get("compare_method", "ahash"))
    methods = [
        ("aHash (Average Hash) - 빠르고 단순", "ahash"),
        ("pHash (Perceptual Hash) - 시각적 유사성에 강함", "phash"),
        ("dHash (Difference Hash) - 구조적 차이 감지", "dhash"),
        ("wHash (Wavelet Hash) - 색상/패턴에 강함", "whash"),
    ]
    for text, val in methods:
        tk.Radiobutton(win, text=text, variable=method_var, value=val).pack(anchor="w")

    # --- 해시 크기 ---
    tk.Label(win, text=lang["ui"].get("hash_size", "해시 크기 (값이 클수록 정밀하지만 느려짐)")).pack(anchor="w")
    size_var = tk.IntVar(value=config.get("hash_size", 8))
    tk.Entry(win, textvariable=size_var).pack(anchor="w")

    # --- 해상도 비율 허용 오차 ---
    tk.Label(win, text=lang["ui"].get("ratio_tolerance", "해상도 비율 허용 오차 (예: 0.02)" )).pack(anchor="w")
    ratio_var = tk.DoubleVar(value=config.get("aspect_ratio_tolerance", 0.02))
    tk.Entry(win, textvariable=ratio_var).pack(anchor="w")

    # --- 오차율 ---
    tk.Label(win, text=lang["ui"].get("tolerance_rate", "허용 오차율(%)")).pack(anchor="w")
    rate_var = tk.DoubleVar(value=config.get("tolerance_rate", 0.05))
    tk.Entry(win, textvariable=rate_var).pack(anchor="w")

    # --- 중복 제한 ---
    tk.Label(win, text=lang["ui"].get("duplicate_limit", "중복 n건 도달시 중단")).pack(anchor="w")
    duplicate_limit_var = tk.IntVar(value=config.get("duplicate_limit_count", 1000))
    tk.Entry(win, textvariable=duplicate_limit_var).pack(anchor="w")

    # --- 해시 일괄 계산 배치 크기 ---
    tk.Label(win, text=lang["ui"].get("hash_batch_size", "한 번에 해시를 계산할 파일 수")).pack(anchor="w")
    batch_size_var = tk.IntVar(value=config.get("hash_precompute_batch_size", 1000))
    tk.Entry(win, textvariable=batch_size_var).pack(anchor="w")

    # --- 최대 해시 계산 파일 수 ---
    tk.Label(win, text=lang["ui"].get("max_hash_compute_files", "최대 해시 계산할 파일 수 (0이면 전체, 이미 계산된 해시 제외)" )).pack(anchor="w")
    max_hash_compute_var = tk.IntVar(value=config.get("max_hash_compute_files", 0))
    tk.Entry(win, textvariable=max_hash_compute_var).pack(anchor="w")

    # --- 최대 비교 파일 수 ---
    tk.Label(win, text=lang["ui"].get("max_compare_files", "비교할 최대 파일 수 (0이면 전체)" )).pack(anchor="w")
    max_files_var = tk.IntVar(value=config.get("max_compare_files", 0))
    tk.Entry(win, textvariable=max_files_var).pack(anchor="w")

    # --- 검색 결과 저장 ---
    save_duplicate_var = tk.BooleanVar(value=config.get("save_duplicate_results", False))
    tk.Checkbutton(win, text=lang["ui"].get("save_duplicate_results", "검색 결과를 자동으로 저장"), variable=save_duplicate_var).pack(anchor="w")

    # --- 이전 검색 결과 불러오기 ---
    load_saved_var = tk.BooleanVar(value=config.get("load_saved_results_on_start", False))
    tk.Checkbutton(win, text=lang["ui"].get("load_saved_results_on_start", "검색 시작 시 이전 결과 불러오기"), variable=load_saved_var).pack(anchor="w")

    auto_open_var = tk.BooleanVar(value=config.get("auto_open_duplicate_results", False))
    tk.Checkbutton(win, text=lang["ui"].get("auto_open_duplicate_results", "중복 결과 검색 후 자동으로 창 열기"), variable=auto_open_var).pack(anchor="w")

    def save():
        config["search_mode"] = mode_var.get()
        config["include_subfolders"] = sub_var.get()
        config["compare_method"] = method_var.get()
        config["hash_size"] = size_var.get()
        config["tolerance_rate"] = rate_var.get()
        config["duplicate_limit_count"] = duplicate_limit_var.get()
        config["hash_precompute_batch_size"] = batch_size_var.get()
        config["max_hash_compute_files"] = max_hash_compute_var.get()
        config["max_compare_files"] = max_files_var.get()
        config["save_duplicate_results"] = save_duplicate_var.get()
        config["load_saved_results_on_start"] = load_saved_var.get()
        config["auto_open_duplicate_results"] = auto_open_var.get()
        config["aspect_ratio_tolerance"] = ratio_var.get()
        save_config(config)
        win.destroy()

    tk.Button(win, text=lang["ui"].get("save", "저장"), command=save).pack()
