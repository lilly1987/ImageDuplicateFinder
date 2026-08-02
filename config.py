import os, locale, yaml

CONFIG_FILE = "config.yml"

def detect_system_lang():
    lang_code, _ = locale.getdefaultlocale()
    if lang_code:
        return lang_code.split("_")[0]
    return "en"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)
