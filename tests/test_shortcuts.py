"""단축키 레지스트리 단위 테스트 (script 스타일)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shortcuts

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  OK  : {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        failures.append(name)


def test_shortcut_to_tk():
    print("[shortcut_to_tk]")
    check("Ctrl+D", shortcuts.shortcut_to_tk("Ctrl+D") == "<Control-d>", shortcuts.shortcut_to_tk("Ctrl+D"))
    check("Shift+Delete", shortcuts.shortcut_to_tk("Shift+Delete") == "<Shift-Delete>", shortcuts.shortcut_to_tk("Shift+Delete"))
    check("F5", shortcuts.shortcut_to_tk("F5") == "<F5>", shortcuts.shortcut_to_tk("F5"))
    check("Escape", shortcuts.shortcut_to_tk("Escape") == "<Escape>", shortcuts.shortcut_to_tk("Escape"))
    check("Ctrl+Alt+X", shortcuts.shortcut_to_tk("Ctrl+Alt+X") == "<Control-Alt-x>", shortcuts.shortcut_to_tk("Ctrl+Alt+X"))
    check("빈 값 → None", shortcuts.shortcut_to_tk("") is None)
    check("공백 → None", shortcuts.shortcut_to_tk("   ") is None)


def test_event_to_shortcut_string():
    print("[event_to_shortcut_string]")

    class FakeEvent:
        def __init__(self, keysym, state):
            self.keysym = keysym
            self.state = state

    # Ctrl+D (state bit 0x4 = Control)
    e = FakeEvent("d", 0x4)
    check("Ctrl+d → Ctrl+D", shortcuts.event_to_shortcut_string(e) == "Ctrl+D",
          shortcuts.event_to_shortcut_string(e))

    # 단독 F5
    e = FakeEvent("F5", 0)
    check("F5 → F5", shortcuts.event_to_shortcut_string(e) == "F5",
          shortcuts.event_to_shortcut_string(e))

    # Shift+Delete (state bit 0x1 = Shift)
    e = FakeEvent("Delete", 0x1)
    check("Shift+Delete", shortcuts.event_to_shortcut_string(e) == "Shift+Delete",
          shortcuts.event_to_shortcut_string(e))

    # 수정자만 → None
    e = FakeEvent("Control_L", 0x4)
    check("Ctrl 만 → None", shortcuts.event_to_shortcut_string(e) is None)


def test_find_duplicate_shortcuts():
    print("[find_duplicate_shortcuts]")
    cfg = {"shortcuts": {"compare": "F5", "remove_selected": "Delete", "open_group_folders": "F5"}}
    dupes = shortcuts.find_duplicate_shortcuts(cfg)
    check("F5 중복 탐지", dupes.get("F5") == ["compare", "open_group_folders"], str(dupes))

    cfg2 = {"shortcuts": {"compare": "F5", "remove_selected": "Delete"}}
    dupes2 = shortcuts.find_duplicate_shortcuts(cfg2)
    check("중복 없음", not dupes2, str(dupes2))

    # 저장값 없으면 기본값 기준으로 중복 검사
    cfg3 = {"shortcuts": {}}
    dupes3 = shortcuts.find_duplicate_shortcuts(cfg3)
    check("기본값 중복 없음", not dupes3, str(dupes3))


def test_action_defs_consistency():
    print("[ACTION_DEFS 일관성]")
    mains = [k for k, v in shortcuts.ACTION_DEFS.items() if v["window"] == "main"]
    results = [k for k, v in shortcuts.ACTION_DEFS.items() if v["window"] == "results"]
    check("main 액션 존재", "compare" in mains)
    check("results 액션 존재", "open_group_folders" in results)
    # label_key가 모두 존재해야 함 (실제 키는 lang에 있지만 최소한 문자열로 정의)
    for k, v in shortcuts.ACTION_DEFS.items():
        check(f"label_key 정의({k})", bool(v.get("label_key")))


test_shortcut_to_tk()
test_event_to_shortcut_string()
test_find_duplicate_shortcuts()
test_action_defs_consistency()

print("\nRESULT:", "ALL OK" if not failures else f"{len(failures)} FAILED")
if failures:
    sys.exit(1)
