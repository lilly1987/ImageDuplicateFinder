"""구문 검증 스크립트 - 수정된 파일들의 문법 오류 확인"""
import py_compile
import sys

files = [
    "hasher.py",
    "state.py",
    "comparator.py",
    "collector.py",
    "ui_options.py",
    "compare.py",
]

ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK  : {f}")
    except py_compile.PyCompileError as e:
        ok = False
        print(f"  FAIL: {f}")
        print(f"        {e}")

print("RESULT:", "ALL OK" if ok else "HAS ERRORS")
sys.exit(0 if ok else 1)