import tkinter as tk
from config import load_config, save_config

def show_options(root, lang):
    win = tk.Toplevel(root)
    win.title(lang["ui"].get("options", "검색/비교 옵션"))
    win.geometry("600x800")

    config = load_config()

    # 스크롤 가능한 프레임 생성
    canvas = tk.Canvas(win)
    scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # --- 검색 모드 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("search_mode", "검색 모드"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    mode_var = tk.StringVar(value=config.get("search_mode", "all_folders"))
    modes = [
        (lang["ui"].get("all_folders", "모든 폴더내 모든 파일 비교"), "all_folders"),
        (lang["ui"].get("per_folder", "각 목록별 폴더내 파일 비교"), "per_folder"),
        (lang["ui"].get("cross_folder", "각 목록의 폴더는 다른 목록 폴더와 비교"), "cross_folder"),
    ]
    for text, val in modes:
        tk.Radiobutton(scrollable_frame, text=text, variable=mode_var, value=val).pack(anchor="w")
    tk.Label(scrollable_frame, text="비교할 폴더 목록의 파일들을 어떻게 비교할지 선택합니다.", fg="gray").pack(anchor="w")

    # --- 하위 폴더 옵션 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("include_subfolders", "하위 폴더 옵션"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    sub_var = tk.StringVar(value=config.get("include_subfolders", "include"))
    subs = [
        (lang["ui"].get("include", "하위 폴더 포함"), "include"),
        (lang["ui"].get("exclude", "하위 폴더 제외"), "exclude"),
    ]
    for text, val in subs:
        tk.Radiobutton(scrollable_frame, text=text, variable=sub_var, value=val).pack(anchor="w")
    tk.Label(scrollable_frame, text="선택한 폴더의 하위 폴더까지 포함하여 파일을 검색할지 선택합니다.", fg="gray").pack(anchor="w")

    # --- 비교 알고리즘 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("compare_method", "비교 알고리즘"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    method_var = tk.StringVar(value=config.get("compare_method", "ahash"))
    methods = [
        ("aHash (Average Hash) - 빠르고 단순", "ahash"),
        ("pHash (Perceptual Hash) - 시각적 유사성에 강함", "phash"),
        ("dHash (Difference Hash) - 구조적 차이 감지", "dhash"),
        ("wHash (Wavelet Hash) - 색상/패턴에 강함", "whash"),
    ]
    for text, val in methods:
        tk.Radiobutton(scrollable_frame, text=text, variable=method_var, value=val).pack(anchor="w")
    tk.Label(scrollable_frame, text="이미지 해시 알고리즘을 선택합니다. 알고리즘마다 유사성 판단 기준이 다릅니다.", fg="gray").pack(anchor="w")

    # --- 해시 크기 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("hash_size", "해시 크기 (값이 클수록 정밀하지만 느려짐)"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    size_var = tk.IntVar(value=config.get("hash_size", 8))
    tk.Entry(scrollable_frame, textvariable=size_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="해시 크기가 클수록 더 정밀하게 비교하지만 계산 시간과 저장 공간이 늘어납니다. (예: 8, 16, 32)", fg="gray").pack(anchor="w")

    # --- 해상도 비율 허용 오차 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("ratio_tolerance", "해상도 비율 허용 오차 (예: 0.02)"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    ratio_var = tk.DoubleVar(value=config.get("aspect_ratio_tolerance", 0.02))
    tk.Entry(scrollable_frame, textvariable=ratio_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="이미지의 가로/세로 비율이 얼마나 달라도 같은 이미지로 볼지 결정합니다. 0.02는 2% 차이까지 허용합니다.", fg="gray").pack(anchor="w")

    # --- 오차율 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("tolerance_rate", "허용 오차율(%)"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    rate_var = tk.DoubleVar(value=config.get("tolerance_rate", 0.05))
    tk.Entry(scrollable_frame, textvariable=rate_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="해시 값이 얼마나 달라도 중복으로 판정할지 결정합니다. 0.05는 5% 차이까지 허용합니다. 0이면 완전히 같은 해시만 중복으로 판정합니다.", fg="gray").pack(anchor="w")

    # --- 중복 제한 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("duplicate_limit", "중복 n건 도달시 중단"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    duplicate_limit_var = tk.IntVar(value=config.get("duplicate_limit_count", 1000))
    tk.Entry(scrollable_frame, textvariable=duplicate_limit_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="중복이 n건 발견되면 비교를 중단합니다. 0이면 중단 없이 전체를 비교합니다.", fg="gray").pack(anchor="w")

    # --- 해시 일괄 계산 배치 크기 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("hash_batch_size", "한 번에 해시를 계산할 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    batch_size_var = tk.IntVar(value=config.get("hash_precompute_batch_size", 1000))
    tk.Entry(scrollable_frame, textvariable=batch_size_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="해시 계산 시 한 번에 처리할 파일 수입니다. 값이 클수록 메모리를 많이 사용하지만 처리 속도가 빨라집니다.", fg="gray").pack(anchor="w")

    # --- 최대 해시 계산 파일 수 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("max_hash_compute_files", "최대 새로 해시 계산할 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    max_hash_compute_var = tk.IntVar(value=config.get("max_hash_compute_files", 0))
    tk.Entry(scrollable_frame, textvariable=max_hash_compute_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="0 초과 시 기존에 계산된 해시는 건너뛰고, 추가로 계산할 해시 갯수만큼만 계산합니다. 0이면 전체를 계산합니다.", fg="gray").pack(anchor="w")

    # --- 최대 비교 파일 수 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("max_compare_files", "추가로 비교할 최대 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    max_files_var = tk.IntVar(value=config.get("max_compare_files", 0))
    tk.Entry(scrollable_frame, textvariable=max_files_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="0 초과 시 기존에 비교한 파일쌍은 건너뛰고, 추가로 비교할 파일 갯수만큼만 비교합니다. 0이면 전체를 비교합니다.", fg="gray").pack(anchor="w")

    # --- 비교 캐시 사용 ---
    use_compare_cache_var = tk.BooleanVar(value=config.get("use_compare_cache", True))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("use_compare_cache", "비교 결과 캐시 사용 (중단 후 이어하기)"), variable=use_compare_cache_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 결과를 DB에 저장하여, 프로그램을 껐다 켜도 기존 비교건을 건너뛰고 신규 파일만 비교합니다.", fg="gray").pack(anchor="w")

    # --- 검색 결과 저장 ---
    save_duplicate_var = tk.BooleanVar(value=config.get("save_duplicate_results", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("save_duplicate_results", "검색 결과를 자동으로 저장"), variable=save_duplicate_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 완료 후 중복 결과를 JSON 파일로 저장합니다.", fg="gray").pack(anchor="w")

    # --- 이전 검색 결과 불러오기 ---
    load_saved_var = tk.BooleanVar(value=config.get("load_saved_results_on_start", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("load_saved_results_on_start", "검색 시작 시 이전 결과 불러오기"), variable=load_saved_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 시작 시 이전에 저장된 중복 결과를 불러와서 이어서 비교합니다.", fg="gray").pack(anchor="w")

    auto_open_var = tk.BooleanVar(value=config.get("auto_open_duplicate_results", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("auto_open_duplicate_results", "중복 결과 검색 후 자동으로 창 열기"), variable=auto_open_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 완료 후 중복 결과 창을 자동으로 엽니다.", fg="gray").pack(anchor="w")

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
        config["use_compare_cache"] = use_compare_cache_var.get()
        config["save_duplicate_results"] = save_duplicate_var.get()
        config["load_saved_results_on_start"] = load_saved_var.get()
        config["auto_open_duplicate_results"] = auto_open_var.get()
        config["aspect_ratio_tolerance"] = ratio_var.get()
        save_config(config)
        win.destroy()

    tk.Button(scrollable_frame, text=lang["ui"].get("save", "저장"), command=save).pack(pady=(20, 10))