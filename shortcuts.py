"""
단축키 레지스트리 모듈.

- 행동(action)별로 콜백을 등록하고, 창별로 Tk 바인딩을 수행
- 설정(config.yml)에서 단축키를 로드/저장
- 설정 창에서 단축키를 편집할 수 있는 UI 유틸 제공
"""


# ──────────────────────────────────────────────────
# 행동 정의: { action_key: { default, window, label_key } }
#   window  : "main" = 메인 윈도우, "results" = 결과창
#   default : 기본 단축키 문자열 또는 None(없음)
# ──────────────────────────────────────────────────
ACTION_DEFS = {
    # ── 메인 창 ──
    "add_folder":          {"default": None,        "window": "main",    "label_key": "add_folder"},
    "compare":             {"default": "F5",         "window": "main",    "label_key": "compare_button"},
    "stop":                {"default": "Escape",     "window": "main",    "label_key": "stop_button"},
    "clear_list":          {"default": None,         "window": "main",    "label_key": "clear_list"},
    "remove_empty_folders":{"default": None,         "window": "main",    "label_key": "remove_empty_folders"},
    "options":             {"default": None,         "window": "main",    "label_key": "options_button"},
    "results":             {"default": None,         "window": "main",    "label_key": "duplicate_results_window"},
    "settings":            {"default": None,         "window": "main",    "label_key": "settings_button"},
    "db_manager":          {"default": None,         "window": "main",    "label_key": "db_manager_button"},
    "refresh_cache":       {"default": None,         "window": "main",    "label_key": "cache_refresh"},
    # ── 결과창 ──
    "remove_missing":      {"default": None,         "window": "results", "label_key": "remove_missing"},
    "remove_selected":     {"default": "Delete",     "window": "results", "label_key": "remove_selected"},
    "delete_selected":     {"default": "Shift+Delete","window": "results", "label_key": "delete_selected_items"},
    "select_all":          {"default": None,         "window": "results", "label_key": "select_all"},
    "deselect_all":        {"default": None,         "window": "results", "label_key": "deselect_all"},
    "invert_selection":    {"default": None,         "window": "results", "label_key": "invert_selection"},
    "open_group_folders":  {"default": "Ctrl+D",     "window": "results", "label_key": "open_group_folders"},
    "refresh_results":     {"default": None,         "window": "results", "label_key": "refresh_results"},
}


# ──────────────────────────────────────────────────
# 콜백 레지스트리 (행동 키 → callable)
# ──────────────────────────────────────────────────
_action_callbacks: dict[str, callable] = {}


def register_action(action_key: str, callback: callable):
    """행동의 콜백을 등록한다."""
    _action_callbacks[action_key] = callback


def unregister_action(action_key: str):
    """행동의 콜백을 제거한다."""
    _action_callbacks.pop(action_key, None)


# ──────────────────────────────────────────────────
# Tk 이벤트 문자열 변환
# ──────────────────────────────────────────────────
# "Ctrl+D" → "<Control-d>"
# "Shift+Delete" → "<Shift-Delete>"
# "F5" → "<F5>"
# "Escape" → "<Escape>"

_SPECIAL_KEYS = {
    "escape": "Escape",
    "delete": "Delete",
    "backspace": "BackSpace",
    "return": "Return", "enter": "Return",
    "tab": "Tab",
    "space": "space",
    "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "insert": "Insert",
}

_MODIFIER_MAP = {
    "ctrl": "Control", "control": "Control",
    "alt": "Alt",
    "shift": "Shift",
}


def shortcut_to_tk(shortcut: str) -> str | None:
    """사람이 읽는 단축키 문자열을 Tk 이벤트 문자열로 변환.
    유효하지 않으면 None."""
    if not shortcut or not shortcut.strip():
        return None
    parts = [p.strip() for p in shortcut.split("+")]
    modifiers = []
    key = None
    for part in parts:
        low = part.lower()
        if low in _MODIFIER_MAP:
            modifiers.append(_MODIFIER_MAP[low])
        else:
            key = part  # 마지막 하나가 키
    if key is None:
        return None
    # 단일 특수 키
    key_lower = key.lower()
    if key_lower in _SPECIAL_KEYS:
        key = _SPECIAL_KEYS[key_lower]
    elif len(key) == 1 and key.isascii() and key.isalpha():
        # 단일 문자: Tk 바인딩은 keysym 소문자와 매칭되므로 소문자 사용
        # (실제 Ctrl+D 키 입력은 keysym 'd'로 보고됨)
        key = key.lower()
    elif key_lower.startswith("f") and key_lower[1:].isdigit():
        key = key  # F1~F12
    else:
        # 기타: 그대로 사용
        pass

    if modifiers:
        return "<" + "-".join(modifiers) + "-" + key + ">"
    else:
        return "<" + key + ">"


def event_to_shortcut_string(event) -> str | None:
    """tkinter KeyPress 이벤트로부터 사람이 읽는 단축키 문자열 생성.
    수정자만 누른 경우 None."""
    mods = []
    if event.state & 0x4:  # Control
        mods.append("Ctrl")
    if event.state & 0x1:  # Shift
        mods.append("Shift")
    if event.state & 0x8:  # Alt
        mods.append("Alt")

    # 키 이름 매핑
    _key_names = {
        "Escape": "Escape", "Delete": "Delete", "BackSpace": "BackSpace",
        "Return": "Enter", "Tab": "Tab", "space": "Space",
        "Home": "Home", "End": "End",
        "Prior": "PageUp", "Next": "PageDown",
        "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
        "Insert": "Insert",
    }
    key = event.keysym
    if key in ("Control_L", "Control_R", "Shift_L", "Shift_R",
               "Alt_L", "Alt_R", "Super_L", "Super_R"):
        return None  # 수정자만
    display_key = _key_names.get(key, key)

    # 단일 문자 대문자 정규화 (Ctrl+A → Ctrl+A)
    if len(display_key) == 1 and "Ctrl" in mods:
        display_key = display_key.upper()
    elif len(display_key) == 1 and "Shift" not in mods:
        display_key = display_key.lower()

    if mods:
        return "+".join(mods) + "+" + display_key
    return display_key



# ──────────────────────────────────────────────────
# 창별 바인딩 / 해제
# ──────────────────────────────────────────────────
# { window_id(ttk widget id): { action_key: event_seq } }
_bound: dict[int, dict[str, str]] = {}


def _make_handler(action_key: str):
    """행동 콜백을 호출하는 Tk 이벤트 핸들러 클로저."""
    def handler(event):
        cb = _action_callbacks.get(action_key)
        if cb:
            cb()
        return "break"  # 이벤트 전파 방지
    return handler


def apply_shortcuts(window, config: dict, window_type: str):
    """config에서 단축키를 읽어 해당 창에만 바인딩."""
    shortcuts = config.get("shortcuts", {})
    win_id = id(window)

    # 기존 바인딩 먼저 해제 (unbind가 _bound[win_id]를 제거하므로 그 뒤에 재생성)
    _unbind_shortcuts(window, window_type)
    _bound[win_id] = {}

    for action_key, info in ACTION_DEFS.items():
        if info["window"] != window_type:
            continue
        # 저장값이 있으면 그 값 사용(빈 문자열 = 비활성화),
        # 저장값이 없으면 기본값 사용
        if action_key in shortcuts:
            raw = shortcuts[action_key] or ""
        else:
            raw = info["default"] or ""
        tk_seq = shortcut_to_tk(raw)
        if not tk_seq or action_key not in _action_callbacks:
            continue
        handler = _make_handler(action_key)
        window.bind(tk_seq, handler)
        _bound[win_id][action_key] = tk_seq


def unbind_shortcuts(window, window_type: str):
    """해당 창의 모든 단축키 바인딩을 해제."""
    _unbind_shortcuts(window, window_type)


def _unbind_shortcuts(window, window_type: str):
    """해당 창의 모든 단축키 바인딩을 해제."""
    win_id = id(window)
    existing = _bound.pop(win_id, {})
    for tk_seq in existing.values():
        try:
            window.unbind(tk_seq)
        except Exception:
            pass


def unregister_all(window_type: str):
    """특정 창 종류에 속한 행동의 콜백을 모두 제거한다."""
    to_remove = [
        k for k, v in ACTION_DEFS.items()
        if v["window"] == window_type
    ]
    for k in to_remove:
        _action_callbacks.pop(k, None)


# ──────────────────────────────────────────────────
# 설정 창 UI 유틸: 키 입력 캡처 Entry
# ──────────────────────────────────────────────────
def make_shortcut_capture_entry(parent, initial: str = "", on_change=None):
    """단축키를 캡처하는 Entry 위젯 생성.
    포커스 시 키 입력을 받아 단축키 문자열로 저장.
    on_change(new_shortcut_str) 콜백 호출."""
    import tkinter as tk
    entry = tk.Entry(parent, width=20, justify="center")
    entry.insert(0, initial or "")
    entry.configure(state="readonly")

    def on_focus_in(e):
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.configure(fg="blue")

    def on_focus_out(e):
        entry.configure(state="readonly")
        entry.configure(fg="black")

    def _on_key(e):
        shortcut = event_to_shortcut_string(e)
        entry.delete(0, "end")
        if shortcut:
            entry.insert(0, shortcut)
        if on_change:
            on_change(shortcut or "")
        return "break"

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    entry.bind("<KeyPress>", _on_key)
    return entry


# ──────────────────────────────────────────────────
# 유틸: 중복 키 검사
# ──────────────────────────────────────────────────
def find_duplicate_shortcuts(config: dict) -> dict[str, list[str]]:
    """config의 shortcuts에서 중복된 키 조합을 찾아 반환.
    반환: { "Ctrl+D": ["open_group_folders", "some_other"], ... }"""
    shortcuts = config.get("shortcuts", {})
    key_to_actions: dict[str, list[str]] = {}
    for action_key, info in ACTION_DEFS.items():
        # 명시적으로 저장된 값 우선, 없으면 기본값
        if action_key in shortcuts:
            raw = shortcuts[action_key] or ""
        else:
            raw = info["default"] or ""
        if raw:
            key_to_actions.setdefault(raw, []).append(action_key)
    return {k: v for k, v in key_to_actions.items() if len(v) > 1}

