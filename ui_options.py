import tkinter as tk
from tkinter import ttk
from config import load_config, save_config
from tooltip import add_tooltip

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
    tk.Label(scrollable_frame, text="비교할 폴더 목록의 파일들을 어떻게 비교할지 선택합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 하위 폴더 옵션 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("include_subfolders", "하위 폴더 옵션"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    sub_var = tk.StringVar(value=config.get("include_subfolders", "include"))
    subs = [
        (lang["ui"].get("include", "하위 폴더 포함"), "include"),
        (lang["ui"].get("exclude", "하위 폴더 제외"), "exclude"),
    ]
    for text, val in subs:
        tk.Radiobutton(scrollable_frame, text=text, variable=sub_var, value=val).pack(anchor="w")
    tk.Label(scrollable_frame, text="선택한 폴더의 하위 폴더까지 포함하여 파일을 검색할지 선택합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 비교 알고리즘 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("compare_method", "비교 알고리즘"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    method_var = tk.StringVar(value=config.get("compare_method", "ahash"))
    methods = [
        ("aHash (Average Hash) - 빠르고 단순", "ahash"),
        ("pHash (Perceptual Hash) - 시각적 유사성에 강함", "phash"),
        ("dHash (Difference Hash) - 구조적 차이 감지", "dhash"),
        ("wHash (Wavelet Hash) - 색상/패턴에 강함", "whash"),
        ("bHash (Block Hash) - 블록 밝기 비교, 256x256 지원", "bhash"),
    ]
    for text, val in methods:
        tk.Radiobutton(scrollable_frame, text=text, variable=method_var, value=val).pack(anchor="w")
    tk.Label(scrollable_frame, text="이미지 해시 알고리즘을 선택합니다. 알고리즘마다 유사성 판단 기준이 다릅니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 해시 크기 (알고리즘별 지원 크기 - 드롭다운) ---
    tk.Label(scrollable_frame, text=lang["ui"].get("hash_size", "해시 크기 (값이 클수록 정밀하지만 느려짐)"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    # 알고리즘별 지원 해시 크기
    hash_size_by_method = {
        "ahash": [8, 16, 32, 64, 128, 256],
        "phash": [8, 16, 32, 64],
        "dhash": [8, 16, 32, 64, 128, 256],
        "whash": [8, 16, 32, 64],
        "bhash": [8, 16, 32, 64, 128, 256],
    }
    config_method = config.get("compare_method", "ahash")
    config_hash_size = config.get("hash_size", 8)
    # config 값이 현재 알고리즘의 목록에 없으면 가장 가까운 상위 값으로 보정
    current_options = hash_size_by_method.get(config_method, [8, 16, 32, 64, 128, 256])
    if config_hash_size not in current_options:
        config_hash_size = next((s for s in current_options if s >= config_hash_size), current_options[-1])
    size_var = tk.IntVar(value=config_hash_size)
    size_combo = ttk.Combobox(
        scrollable_frame,
        textvariable=size_var,
        values=current_options,
        state="readonly",
        width=10,
    )
    size_combo.pack(anchor="w")
    tk.Label(scrollable_frame, text="알고리즘별로 지원하는 해시 크기가 다릅니다. 클수록 더 정밀하지만 계산 시간과 저장 공간이 늘어납니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # 알고리즘 변경 시 해시 크기 목록 동기화
    def update_hash_size_options(*args):
        method = method_var.get()
        options = hash_size_by_method.get(method, [8, 16, 32, 64, 128, 256])
        size_combo["values"] = options
        # 현재 값이 새 목록에 없으면 보정
        current = size_var.get()
        if current not in options:
            size_var.set(next((s for s in options if s >= current), options[-1]))

    method_var.trace("w", update_hash_size_options)
    # 초기 적용
    update_hash_size_options()

    # --- 해상도 비율 허용 오차 ---
    ratio_header = tk.Frame(scrollable_frame)
    ratio_header.pack(anchor="w", pady=(10, 2), fill="x")
    tk.Label(ratio_header, text=lang["ui"].get("ratio_tolerance", "해상도 비율 허용 오차 (예: 0.02)"), font=("Arial", 10, "bold")).pack(side="left")
    use_aspect_ratio_var = tk.BooleanVar(value=config.get("use_aspect_ratio", True))
    tk.Checkbutton(ratio_header, text=lang["ui"].get("use_check", "사용"), variable=use_aspect_ratio_var).pack(side="left", padx=(8, 0))
    ratio_var = tk.DoubleVar(value=config.get("aspect_ratio_tolerance", 0.02))
    tk.Entry(scrollable_frame, textvariable=ratio_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="이미지의 가로/세로 비율이 얼마나 달라도 같은 이미지로 볼지 결정합니다. 0.02는 2% 차이까지 허용합니다. 체크 해제 시 모든 비율을 허용합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 오차값 (정수형 해밍 거리) ---
    tol_header = tk.Frame(scrollable_frame)
    tol_header.pack(anchor="w", pady=(10, 2), fill="x")
    tk.Label(tol_header, text=lang["ui"].get("tolerance_rate", "허용 오차 (해밍 거리)"), font=("Arial", 10, "bold")).pack(side="left")
    use_tolerance_var = tk.BooleanVar(value=config.get("use_tolerance", True))
    tk.Checkbutton(tol_header, text=lang["ui"].get("use_check", "사용"), variable=use_tolerance_var).pack(side="left", padx=(8, 0))
    # 기존 tolerance_rate(비율)를 정수 해밍 거리로 변환
    current_hash_size = int(size_var.get())
    current_rate = float(config.get("tolerance_rate", 0.05))
    # 비율 → 정수 해밍 거리로 변환
    default_hamming = max(0, min(current_hash_size * current_hash_size, int(round(current_rate * current_hash_size * current_hash_size))))
    # 이미 정수로 저장된 경우 (tolerance_hamming)
    rate_var = tk.IntVar(value=config.get("tolerance_hamming", default_hamming))
    tk.Entry(scrollable_frame, textvariable=rate_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="해시 값이 몇 비트까지 달라도 중복으로 판정할지 정수로 입력합니다. (예: 0 = 완전히 같은 해시만, 2 = 비트 2개까지 허용) 체크 해제 시 완전히 같은 해시만 중복으로 판정합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 중복 제한 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("duplicate_limit", "중복 n건 도달시 중단"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    duplicate_limit_var = tk.IntVar(value=config.get("duplicate_limit_count", 1000))
    tk.Entry(scrollable_frame, textvariable=duplicate_limit_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="중복이 n건 발견되면 비교를 중단합니다. 0이면 중단 없이 전체를 비교합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 해시 일괄 계산 배치 크기 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("hash_batch_size", "한 번에 해시를 계산할 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    batch_size_var = tk.IntVar(value=config.get("hash_precompute_batch_size", 1000))
    tk.Entry(scrollable_frame, textvariable=batch_size_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="해시 계산 시 한 번에 처리할 파일 수입니다. 값이 클수록 메모리를 많이 사용하지만 처리 속도가 빨라집니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 최대 해시 계산 파일 수 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("max_hash_compute_files", "최대 새로 해시 계산할 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    max_hash_compute_var = tk.IntVar(value=config.get("max_hash_compute_files", 0))
    tk.Entry(scrollable_frame, textvariable=max_hash_compute_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="0 초과 시 기존에 계산된 해시는 건너뛰고, 추가로 계산할 해시 갯수만큼만 계산합니다. 0이면 전체를 계산합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 최대 비교 파일 수 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("max_compare_files", "추가로 비교할 최대 파일 수"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    max_files_var = tk.IntVar(value=config.get("max_compare_files", 0))
    tk.Entry(scrollable_frame, textvariable=max_files_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="0 초과 시 기존에 비교한 파일쌍은 건너뛰고, 추가로 비교할 파일 갯수만큼만 비교합니다. 0이면 전체를 비교합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 최대 메모리 사용량 ---
    tk.Label(scrollable_frame, text=lang["ui"].get("max_memory_mb", "비교 캐시 메모리 사용량 (MB)"), font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 2))
    max_memory_var = tk.IntVar(value=config.get("max_memory_mb", 0))
    tk.Entry(scrollable_frame, textvariable=max_memory_var).pack(anchor="w")
    tk.Label(scrollable_frame, text="비교 결과 캐시를 메모리에 로드할 때 사용할 최대 메모리(MB)입니다. 0이면 전체를 로드합니다. 시스템 메모리를 고려해 설정하세요.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 비교 캐시 사용 ---
    use_compare_cache_var = tk.BooleanVar(value=config.get("use_compare_cache", True))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("use_compare_cache", "비교 결과 캐시 사용 (중단 후 이어하기)"), variable=use_compare_cache_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 결과를 DB에 저장하여, 프로그램을 껐다 켜도 기존 비교건을 건너뛰고 신규 파일만 비교합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 검색 결과 저장 ---
    save_duplicate_var = tk.BooleanVar(value=config.get("save_duplicate_results", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("save_duplicate_results", "검색 결과를 자동으로 저장"), variable=save_duplicate_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 완료 후 중복 결과를 JSON 파일로 저장합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    # --- 이전 검색 결과 불러오기 ---
    load_saved_var = tk.BooleanVar(value=config.get("load_saved_results_on_start", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("load_saved_results_on_start", "검색 시작 시 이전 결과 불러오기"), variable=load_saved_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 시작 시 이전에 저장된 중복 결과를 불러와서 이어서 비교합니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    auto_open_var = tk.BooleanVar(value=config.get("auto_open_duplicate_results", False))
    tk.Checkbutton(scrollable_frame, text=lang["ui"].get("auto_open_duplicate_results", "중복 결과 검색 후 자동으로 창 열기"), variable=auto_open_var).pack(anchor="w", pady=(10, 2))
    tk.Label(scrollable_frame, text="비교 완료 후 중복 결과 창을 자동으로 엽니다.", fg="gray", wraplength=550, justify="left").pack(anchor="w", fill="x")

    def save():
        config["search_mode"] = mode_var.get()
        config["include_subfolders"] = sub_var.get()
        config["compare_method"] = method_var.get()
        config["hash_size"] = size_var.get()
        # 정수 해밍 거리를 tolerance_hamming으로 저장
        # 기존 tolerance_rate(비율)도 역산하여 저장 (하위 호환)
        hamming = rate_var.get()
        config["tolerance_hamming"] = hamming
        hs = size_var.get()
        config["tolerance_rate"] = (hamming / (hs * hs)) if hs > 0 else 0.0
        config["duplicate_limit_count"] = duplicate_limit_var.get()
        config["hash_precompute_batch_size"] = batch_size_var.get()
        config["max_hash_compute_files"] = max_hash_compute_var.get()
        config["max_compare_files"] = max_files_var.get()
        config["max_memory_mb"] = max_memory_var.get()
        config["use_compare_cache"] = use_compare_cache_var.get()
        config["save_duplicate_results"] = save_duplicate_var.get()
        config["load_saved_results_on_start"] = load_saved_var.get()
        config["auto_open_duplicate_results"] = auto_open_var.get()
        config["aspect_ratio_tolerance"] = ratio_var.get()
        config["use_aspect_ratio"] = use_aspect_ratio_var.get()
        config["use_tolerance"] = use_tolerance_var.get()
        save_config(config)
        win.destroy()

    save_btn = tk.Button(scrollable_frame, text=lang["ui"].get("save", "저장"), command=save)
    save_btn.pack(pady=(20, 10))
    add_tooltip(save_btn, lang["ui"].get("tooltip_save_options", ""))
