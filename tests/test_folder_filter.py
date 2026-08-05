"""ui_results.py의 폴더 체크 상태 필터 로직 단위 테스트"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_in_checked_folder(file_path, checked_folders):
    """파일이 체크된 폴더(또는 그 하위)에 속하는지 확인"""
    if not checked_folders:
        return True
    file_path_lower = file_path.lower()
    for folder in checked_folders:
        folder_lower = folder.lower()
        if file_path_lower == folder_lower or file_path_lower.startswith(folder_lower + os.sep) or file_path_lower.startswith(folder_lower + "/"):
            return True
    return False


def _filter_groups_by_folder(groups, checked_folders):
    """체크 해제된 폴더의 파일을 그룹에서 제외 (그룹이 1개 이하가 되면 그룹 제외)"""
    if not checked_folders or not groups:
        return groups
    result = []
    for group in groups:
        filtered = [p for p in group if _is_in_checked_folder(p, checked_folders)]
        if len(filtered) > 1:
            result.append(filtered)
    return result


def test_checked_folder_only_group_kept():
    """체크된 폴더만 있는 그룹 → 유지"""
    checked = {r"C:\Users\test\folder1", r"C:\Users\test\folder2"}
    g1 = [r"C:\Users\test\folder1\a.jpg", r"C:\Users\test\folder1\b.jpg"]
    result = _filter_groups_by_folder([g1], checked)
    assert result == [g1], f"FAIL: {result}"


def test_unchecked_folder_only_group_removed():
    """체크 해제된 폴더 파일만 있는 그룹 → 제거"""
    checked = {r"C:\Users\test\folder1", r"C:\Users\test\folder2"}
    g2 = [r"C:\Users\test\folder3\a.jpg", r"C:\Users\test\folder3\b.jpg"]
    result = _filter_groups_by_folder([g2], checked)
    assert result == [], f"FAIL: {result}"


def test_mixed_group_keeps_only_checked_files():
    """혼합 그룹 → 체크된 폴더 파일만 남김"""
    checked = {r"C:\Users\test\folder1", r"C:\Users\test\folder2"}
    g3 = [
        r"C:\Users\test\folder1\a.jpg",
        r"C:\Users\test\folder3\b.jpg",
        r"C:\Users\test\folder1\c.jpg",
    ]
    result = _filter_groups_by_folder([g3], checked)
    assert result == [[r"C:\Users\test\folder1\a.jpg", r"C:\Users\test\folder1\c.jpg"]], f"FAIL: {result}"


def test_single_remaining_file_group_removed():
    """체크 해제 폴더 + 체크 폴더 1개 → 1개만 남아 그룹 제거"""
    checked = {r"C:\Users\test\folder1", r"C:\Users\test\folder2"}
    g4 = [r"C:\Users\test\folder1\a.jpg", r"C:\Users\test\folder3\b.jpg"]
    result = _filter_groups_by_folder([g4], checked)
    assert result == [], f"FAIL: {result}"


def test_nested_subfolder_kept():
    """체크된 폴더의 하위 하위 경로 → 유지"""
    checked = {r"C:\Users\test\folder1", r"C:\Users\test\folder2"}
    g5 = [
        r"C:\Users\test\folder1\sub\deep\a.jpg",
        r"C:\Users\test\folder1\sub\b.jpg",
    ]
    result = _filter_groups_by_folder([g5], checked)
    assert result == [g5], f"FAIL: {result}"


def test_no_filter_all_groups_kept():
    """체크 폴더가 없으면 모든 그룹 유지"""
    groups = [
        [r"C:\Users\test\folder1\a.jpg", r"C:\Users\test\folder1\b.jpg"],
        [r"C:\Users\test\folder3\a.jpg", r"C:\Users\test\folder3\b.jpg"],
    ]
    result = _filter_groups_by_folder(groups, set())
    assert result == groups, f"FAIL: {result}"


def test_empty_groups():
    """빈 그룹 리스트"""
    result = _filter_groups_by_folder([], {r"C:\Users\test\folder1"})
    assert result == [], f"FAIL: {result}"


def test_forward_slash_path_match():
    """슬래시 경로도 매칭"""
    checked = {r"C:/Users/test/folder1"}
    g = [r"C:/Users/test/folder1/a.jpg", r"C:/Users/test/folder1/b.jpg"]
    result = _filter_groups_by_folder([g], checked)
    assert result == [g], f"FAIL: {result}"


if __name__ == "__main__":
    tests = [
        test_checked_folder_only_group_kept,
        test_unchecked_folder_only_group_removed,
        test_mixed_group_keeps_only_checked_files,
        test_single_remaining_file_group_removed,
        test_nested_subfolder_kept,
        test_no_filter_all_groups_kept,
        test_empty_groups,
        test_forward_slash_path_match,
    ]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"OK  : {t.__name__}")
    print(f"RESULT: ALL OK ({passed} passed)")