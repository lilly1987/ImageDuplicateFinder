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
    "total": "Total"
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
