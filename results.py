"""
중복 결과 저장/로드 모듈.

중복 결과를 텍스트/JSON 파일 또는 DB로 저장하고 불러오는 기능.
"""

import glob
import json
import os
from datetime import datetime

from logger import logger
from comparator import build_groups, load_duplicate_results_from_db
from state import duplicate_pairs, duplicates_lock


# ============================================================
# 결과 파일 저장/로드 (JSON 호환 유지)
# ============================================================
def _search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance):
    """검색 옵션 기반 파일명 접미사 생성"""
    ratio_str = str(round(aspect_ratio_tol, 4)).replace('.', 'p')
    tol_str = str(int(tolerance))
    return f"{method}_h{hash_size}_ratio{ratio_str}_tol{tol_str}"


def duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance):
    """중복 결과 JSON 파일 경로"""
    return f"duplicate_results_{_search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance)}.json"


def resolve_search_options(method=None, hash_size=None, aspect_ratio_tol=None, tolerance=None):
    """검색 옵션 해석 (기본값은 config에서)"""
    if method is None or hash_size is None or aspect_ratio_tol is None or tolerance is None:
        from config import load_config
        options = load_config()
        method = method if method is not None else options.get("compare_method", "ahash")
        hash_size = hash_size if hash_size is not None else int(options.get("hash_size", 8))
        aspect_ratio_tol = aspect_ratio_tol if aspect_ratio_tol is not None else float(options.get("aspect_ratio_tolerance", 0.02))
        tolerance_hamming = options.get("tolerance_hamming", 0)
        try:
            tolerance_hamming = int(tolerance_hamming)
        except Exception:
            tolerance_hamming = 0
        tolerance = tolerance if tolerance is not None else max(0, min(hash_size * hash_size, tolerance_hamming))
    return method, int(hash_size), float(aspect_ratio_tol), int(tolerance)


def format_result_filename(method, hash_size, aspect_ratio_tol, tolerance):
    """결과 텍스트 파일명 생성"""
    return f"result_{_search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"


def write_result_file_if_any(method, hash_size, aspect_ratio_tol, tolerance):
    """중복 결과를 텍스트 파일로 저장"""
    with duplicates_lock:
        pairs = set(duplicate_pairs)
    if not pairs:
        return None
    groups = build_groups(pairs)
    fn = format_result_filename(method, hash_size, aspect_ratio_tol, tolerance)
    path = os.path.join(os.getcwd(), fn)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"Search Options: method={method}, hash_size={hash_size}, aspect_ratio_tol={aspect_ratio_tol}, tolerance={tolerance}\n")
            fh.write("\n")
            for i, g in enumerate(groups, start=1):
                fh.write(f"Group {i}:\n")
                for p in sorted(g):
                    fh.write(p + "\n")
                fh.write("\n")
        return path
    except Exception:
        return None


def save_duplicate_results_json(method=None, hash_size=None, aspect_ratio_tol=None, tolerance=None):
    """중복 결과를 JSON 파일로 저장"""
    method, hash_size, aspect_ratio_tol, tolerance = resolve_search_options(
        method, hash_size, aspect_ratio_tol, tolerance
    )
    with duplicates_lock:
        if not duplicate_pairs:
            return None
        groups = build_groups(duplicate_pairs)
    data = {
        "saved_at": datetime.now().isoformat(),
        "search_options": {
            "method": method,
            "hash_size": hash_size,
            "aspect_ratio_tol": aspect_ratio_tol,
            "tolerance": tolerance,
        },
        "groups": [sorted(list(g)) for g in groups],
    }
    path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _parse_duplicate_text_file(path):
    """텍스트 결과 파일 파싱"""
    groups = []
    current = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.lower().startswith("group") and stripped.endswith(":"):
                    current = []
                    groups.append(current)
                elif current is not None:
                    current.append(stripped)
        return [g for g in groups if len(g) > 1]
    except Exception:
        return []


def _load_groups_from_json(path, method, hash_size, aspect_ratio_tol, tolerance):
    """JSON 결과 파일에서 그룹 로드"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    saved_options = data.get("search_options")
    if saved_options:
        saved_tol = saved_options.get("tolerance", -1)
        try:
            saved_tol = int(saved_tol)
        except Exception:
            saved_tol = -1
        if (
            saved_options.get("method") != method
            or int(saved_options.get("hash_size", -1)) != hash_size
            or round(float(saved_options.get("aspect_ratio_tol", -1)), 4) != round(aspect_ratio_tol, 4)
            or saved_tol != int(tolerance)
        ):
            return None
    groups = data.get("groups", [])
    pairs = set()
    for group in groups:
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                pairs.add(tuple(sorted((group[i], group[j]))))
    with duplicates_lock:
        duplicate_pairs.clear()
        duplicate_pairs.update(pairs)
    return groups


def load_duplicate_results_json(method=None, hash_size=None, aspect_ratio_tol=None, tolerance=None):
    """
    중복 결과 로드.
    우선순위: DB → JSON 파일 → 텍스트 파일
    """
    method, hash_size, aspect_ratio_tol, tolerance = resolve_search_options(
        method, hash_size, aspect_ratio_tol, tolerance
    )

    # 1. DB에서 로드 (실패 시 JSON 파일로 폴백)
    try:
        db_groups = load_duplicate_results_from_db(method, hash_size)
        if db_groups:
            return db_groups
    except Exception:
        pass

    # 2. JSON 파일에서 로드
    json_path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance)
    if os.path.exists(json_path):
        try:
            groups = _load_groups_from_json(json_path, method, hash_size, aspect_ratio_tol, tolerance)
            if groups is not None:
                return groups
        except Exception:
            pass

    # 3. 텍스트 파일에서 로드
    suffix = _search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance)
    txt_pattern = f"result_{suffix}_*.txt"
    txt_files = sorted(glob.glob(txt_pattern), key=os.path.getmtime, reverse=True)
    for txt_path in txt_files:
        groups = _parse_duplicate_text_file(txt_path)
        if groups:
            pairs = set()
            for group in groups:
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        pairs.add(tuple(sorted((group[i], group[j]))))
            with duplicates_lock:
                duplicate_pairs.clear()
                duplicate_pairs.update(pairs)
            return groups
    return None