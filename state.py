"""
공유 전역 상태 모듈.

compare.py 분할 시 순환 import를 피하기 위해
여러 모듈이 공유해야 하는 전역 변수와 락을 한 곳에 모음.
"""

import threading

# 중단 이벤트
stop_event = threading.Event()

# 해시 메모리 캐시: {(path, method, hash_size): hash_value}
hash_memory_cache = {}
hash_memory_lock = threading.Lock()

# 비교 결과 메모리 캐시: {(file1, file2, method, hash_size): is_duplicate}
compare_memory_cache = {}
compare_memory_lock = threading.Lock()

# 중복 결과 쌍 (정렬된 순서로 저장)
duplicate_pairs = set()
duplicates_lock = threading.Lock()

# 비교 캐시 메모리 선로드 여부
_compare_cache_loaded = False
_compare_cache_loaded_lock = threading.Lock()

# 해시 문자열을 정수로 변환하는 전역 캐시
_hash_int_cache = {}
_hash_int_lock = threading.Lock()

# 이미지 크기 캐시: {path: (width, height)}
# - 해시 계산 시점에 얻은 이미지 크기를 저장
# - 해상도 비율(aspect_ratio_tol) 필터링에 사용
image_sizes = {}
image_sizes_lock = threading.Lock()


# ============================================================
# 중단/재시작 제어
# ============================================================
def request_stop():
    """비교 중단 요청"""
    stop_event.set()
    # database 모듈의 db_write 종료 처리는 호출 측에서 수행
    try:
        from database import start_db_writer, db_write_event
        start_db_writer()
        db_write_event.set()
    except Exception:
        pass


def reset_stop():
    """중단 상태 초기화"""
    stop_event.clear()
    with hash_memory_lock:
        hash_memory_cache.clear()
    with compare_memory_lock:
        compare_memory_cache.clear()


def is_stop_requested():
    """중단 요청 여부 확인"""
    return stop_event.is_set()