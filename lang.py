import os, yaml

# UI에서 반드시 필요한 키와 기본 영어값
REQUIRED_KEYS = {
    "options_title": "Search Options",
    "settings_title": "Settings",
    "compare_mode": "Compare Mode",
    "include_subfolders": "Include Subfolders",
    "language": "Language",
    "save": "Save",
    "options_button": "Search Options",
    "settings_button": "Settings",
    "compare_button": "Try Compare",
    "add_folder": "Add Folder",
    "clear_list": "Clear List",
    "total": "Total",
    "options": "Search/Compare Options",
    "search_mode": "Search Mode",
    "all_folders": "Compare all files in all folders",
    "per_folder": "Compare files within each folder",
    "cross_folder": "Compare files only across folders",
    "include": "Include subfolders",
    "exclude": "Exclude subfolders",
    "compare_method": "Compare Algorithm",
    "hash_size": "Hash Size (larger = more precise but slower)",
    "ratio_tolerance": "Resolution ratio tolerance (e.g. 0.02)",
    "tolerance_rate": "Tolerance (Hamming distance)",
    "duplicate_limit": "Stop when duplicate count reached",
    "hash_batch_size": "Hash precompute batch size",
    "max_hash_compute_files": "Max hash compute files (0=all, cached excluded)",
    "max_compare_files": "Max compare files (0=all)",
    "max_memory_mb": "Max memory for compare cache (MB)",
    "use_compare_cache": "Use compare cache (skip already compared)",
    "save_duplicate_results": "Save duplicate results to JSON",
    "load_saved_results_on_start": "Load saved results on start",
    "auto_open_duplicate_results": "Auto-open duplicate results window after search",
    "auto_retry_compare": "Auto-retry when compare work remains",
    "duplicate_results_window": "Duplicate Results",
    "remove_missing": "Remove missing files",
    "remove_selected": "Remove checked groups/items",
    "delete_selected_items": "Delete selected files permanently",
    "delete_files_confirm": "Permanently delete the selected files? This cannot be undone.",
    "delete_files_error": "Some files could not be deleted.",
    "confirm": "Confirm",
    "invert_selection": "Invert selection",
    "preview": "Preview",
    "missing_file": "File not found",
    "info": "Info",
    "no_saved_results": "No saved duplicate results found.",
    "removed_missing": "Missing files removed.",
    "group_count": "Count",
    "group_name": "Group",
    "file_path": "File Path",
    "check": "Check",
    "refresh_results": "Refresh",
    "stop_button": "Stop Compare",
    "cache_manage": "Cache Management",
    "cache_desc": "SQLite cache management",
    "view_cache": "View",
    "clear_cache": "Clear",
    "drop_db": "Delete DB",
    # 툴팁
    "tooltip_add_folder": "Add a folder to the search list. You can drag and drop folders here.",
    "tooltip_clear_list": "Remove all folders from the search list.",
    "tooltip_options": "Configure search and comparison options (algorithm, hash size, tolerance, etc.).",
    "tooltip_settings": "Configure application settings (language, cache, etc.).",
    "tooltip_compare": "Start comparing images in the listed folders.",
    "tooltip_stop": "Stop the current comparison process.",
    "tooltip_cache_refresh": "Refresh cache statistics.",
    "tooltip_cache_clear": "Clear all cached hash and comparison data.",
    "tooltip_cache_drop": "Delete the cache database file (cache.db).",
    "tooltip_duplicate_results": "View and manage duplicate image groups.",
    "tooltip_remove_missing": "Remove files that no longer exist from the duplicate list.",
    "tooltip_remove_selected": "Remove checked groups or items from the list (files are not deleted).",
    "tooltip_delete_selected": "Permanently delete the selected files from disk.",
    "tooltip_invert_selection": "Invert the current selection.",
    "tooltip_refresh_results": "Refresh: apply deletions/removals to the original results, then reload from source.",
    "tooltip_save_options": "Save all option changes to config.yml.",
}


def ensure_lang_file(language: str):
    """언어 파일이 없으면 기본 영어값으로 자동 생성"""
    lang_file = f"lang.{language}.yml"
    if not os.path.exists(lang_file):
        data = {"ui": REQUIRED_KEYS.copy()}
        with open(lang_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)
    return lang_file


def load_lang(language: str):
    # 언어 파일 확인 및 자동 생성
    lang_file = f"lang.{language}.yml"
    if not os.path.exists(lang_file):
        # 해당 언어가 없으면 영어 파일 생성/사용
        lang_file = ensure_lang_file("en")

    with open(lang_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # ui 섹션이 없으면 생성
    if "ui" not in data:
        data["ui"] = {}

    # 빠진 키 보정
    for k, v in REQUIRED_KEYS.items():
        if k not in data["ui"]:
            data["ui"][k] = v

    return data
