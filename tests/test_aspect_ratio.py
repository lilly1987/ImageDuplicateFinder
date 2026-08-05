"""
aspect_ratio 필터링 통합 테스트.

검증 항목:
1. _aspect_ratio_match 함수 기본 동작
2. aspect_ratio_tol >= 1.0 이면 모든 비율 허용 (체크 해제 상태)
3. 크기 모르는 파일은 통과 (엄격한 필터링 방지)
4. 동일 해시지만 비율이 다른 파일 필터링
"""
import os
import sys

# 프로젝트 루트를 paths에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state import image_sizes, image_sizes_lock
from comparator import _aspect_ratio_match


def test_aspect_ratio_match():
    """_aspect_ratio_match 함수 동작 검증"""
    with image_sizes_lock:
        image_sizes.clear()
        # 16:9 (1.777) 이미지
        image_sizes["/a/wide1.jpg"] = (1920, 1080)
        image_sizes["/a/wide2.jpg"] = (1280, 720)
        # 4:3 (1.333) 이미지
        image_sizes["/a/square1.jpg"] = (800, 600)
        image_sizes["/a/square2.jpg"] = (1024, 768)

    # 1. 비율 허용 오차 0.02 - 같은 비율(16:9)끼리는 매치
    assert _aspect_ratio_match("/a/wide1.jpg", "/a/wide2.jpg", 0.02) is True
    print("  OK: 16:9 vs 16:9 @tol=0.02 → True")

    # 2. 비율 허용 오차 0.02 - 16:9 vs 4:3은 매치 안됨
    assert _aspect_ratio_match("/a/wide1.jpg", "/a/square1.jpg", 0.02) is False
    print("  OK: 16:9 vs 4:3 @tol=0.02 → False")

    # 3. 비율 허용 오차 1.0 (체크 해제) - 모든 비율 허용
    assert _aspect_ratio_match("/a/wide1.jpg", "/a/square1.jpg", 1.0) is True
    print("  OK: 16:9 vs 4:3 @tol=1.0 → True")

    # 4. 크기를 모르는 파일은 통과 (해시만으로 비교)
    assert _aspect_ratio_match("/a/wide1.jpg", "/b/unknown.jpg", 0.02) is True
    print("  OK: 크기 모르는 파일 → True (통과)")

    # 5. tol=None 또는 tol>=1.0 인 경우 바로 True
    assert _aspect_ratio_match("/a/wide1.jpg", "/a/square1.jpg", 1.5) is True
    print("  OK: tol=1.5 (>=1.0) → True")

    # 6. 아주 근접한 비율 (0.001 오차 내)
    with image_sizes_lock:
        image_sizes["/a/almost1.jpg"] = (1366, 768)   # 1.7786
        image_sizes["/a/almost2.jpg"] = (1920, 1080)  # 1.7777
    assert _aspect_ratio_match("/a/almost1.jpg", "/a/almost2.jpg", 0.001) is True
    print("  OK: 1.7786 vs 1.7777 @tol=0.001 → True (근접 비율 허용)")

    print("\nALL PASS: _aspect_ratio_match")


if __name__ == "__main__":
    test_aspect_ratio_match()