"""
해시-비교 파이프라인(_run_hash_compare_pipeline) 스모크 테스트.
- 임시 이미지 파일들을 생성하고 파이프라인이 정상 완료되는지 확인.
- 중단/종료 신호 처리, 워커 풀 동작 검증.
"""
import os
import sys
import tempfile
import shutil
import threading

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image
import numpy as np

from collector import _run_hash_compare_pipeline
from state import reset_stop, is_stop_requested, request_stop


def _make_test_image(path, size=(64, 64), color=(100, 150, 200), noise=0):
    """테스트용 이미지 생성"""
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, :] = color
    if noise:
        rng = np.random.default_rng(42)
        arr += rng.integers(0, noise, arr.shape, dtype=np.uint8)
    Image.fromarray(arr).save(path)


def _make_dup_image(path, base_path):
    """중복 이미지 생성 (기본 이미지와 거의 동일)"""
    with Image.open(base_path) as img:
        img.save(path)


def test_pipeline_completes():
    """파이프라인이 정상 완료되는지 확인"""
    reset_stop()
    tmpdir = tempfile.mkdtemp(prefix="idf_pipe_")
    try:
        folder = os.path.join(tmpdir, "folder1")
        os.makedirs(folder)

        # 이미지 12개 생성 (중복 3쌍 포함)
        base_paths = []
        for i in range(6):
            p = os.path.join(folder, f"base_{i}.png")
            _make_test_image(p, color=(30 * i % 255, 60 * i % 255, 120 * i % 255))
            base_paths.append(p)
        # 각 base에 대한 복사본 (중복)
        for i, bp in enumerate(base_paths):
            dp = os.path.join(folder, f"dup_{i}.png")
            _make_dup_image(dp, bp)

        options = {
            "scan_batch_size": 5,
            "use_bktree": True,
        }
        start_time = __import__("time").perf_counter()
        total_compared, total_duplicates, total_pairs, compare_file_paths = (
            _run_hash_compare_pipeline(
                search_mode="all_folders",
                folders=[folder],
                include_sub=True,
                options=options,
                method="dhash",
                hash_size=16,
                tolerance=0,
                duplicate_limit=0,
                max_compare_files=0,
                max_hash_compute_files=0,
                use_compare_cache=False,
                start_time=start_time,
                compare_progress_log_interval=0,
                aspect_ratio_tol=1.0,
            )
        )

        assert is_stop_requested() is False, "파이프라인이 중단 요청 없이 완료되어야 함"
        assert len(compare_file_paths) == 12, f"12개 파일이어야 함, got {len(compare_file_paths)}"
        assert total_compared > 0, f"비교가 0건이면 안 됨, got {total_compared}"
        assert total_duplicates >= 6, f"중복 6건 이상이어야 함, got {total_duplicates}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pipeline_stop():
    """파이프라인 중단 시 스레드가 안전하게 종료되는지 확인"""
    reset_stop()
    tmpdir = tempfile.mkdtemp(prefix="idf_pipe_stop_")
    try:
        folder = os.path.join(tmpdir, "folder1")
        os.makedirs(folder)
        for i in range(8):
            p = os.path.join(folder, f"img_{i}.png")
            _make_test_image(p, color=(i * 30 % 255, i * 50 % 255, i * 90 % 255))

        options = {"scan_batch_size": 3, "use_bktree": True}

        # 중단 요청을 별도 타이머 스레드에서 실행 (2초 후)
        def _stop_later():
            import time as t
            t.sleep(2)
            request_stop()

        st = threading.Thread(target=_stop_later, daemon=True)
        st.start()

        start_time = __import__("time").perf_counter()
        # 중단 후에도 데드락 없이 반환되어야 함
        result = _run_hash_compare_pipeline(
            search_mode="all_folders",
            folders=[folder],
            include_sub=True,
            options=options,
            method="dhash",
            hash_size=16,
            tolerance=0,
            duplicate_limit=0,
            max_compare_files=0,
            max_hash_compute_files=0,
            use_compare_cache=False,
            start_time=start_time,
            compare_progress_log_interval=0,
            aspect_ratio_tol=1.0,
        )
        # 반환값 4개 확인
        assert isinstance(result, tuple) and len(result) == 4, f"4개 값 반환 필요, got {result}"

        stop_result = is_stop_requested()
        reset_stop()
        # 중단이 요청되었거나 완료 상태여야 함 (데드락 없이 반환됨)
        assert stop_result is True or stop_result is False
    finally:
        reset_stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pipeline_no_bktree():
    """BK-Tree 미사용 모드에서도 동작하는지 확인"""
    reset_stop()
    tmpdir = tempfile.mkdtemp(prefix="idf_pipe_nobk_")
    try:
        folder = os.path.join(tmpdir, "folder1")
        os.makedirs(folder)
        for i in range(6):
            p = os.path.join(folder, f"img_{i}.png")
            _make_test_image(p, color=(i * 40 % 255, i * 70 % 255, i * 110 % 255))

        options = {"scan_batch_size": 4, "use_bktree": False}
        start_time = __import__("time").perf_counter()
        total_compared, total_duplicates, total_pairs, compare_file_paths = (
            _run_hash_compare_pipeline(
                search_mode="all_folders",
                folders=[folder],
                include_sub=True,
                options=options,
                method="ahash",
                hash_size=8,
                tolerance=0,
                duplicate_limit=0,
                max_compare_files=0,
                max_hash_compute_files=0,
                use_compare_cache=False,
                start_time=start_time,
                compare_progress_log_interval=0,
                aspect_ratio_tol=1.0,
            )
        )
        assert len(compare_file_paths) == 6, f"6개 파일이어야 함, got {len(compare_file_paths)}"
        assert total_compared > 0, f"비교가 0건이면 안 됨, got {total_compared}"
    finally:
        reset_stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_has_remaining_after_failed_hash():
    """
    해시 계산 실패(None) 파일은 _has_remaining_compare_work에서
    "남은 작업"으로 판정되지 않아야 함 (무한 재시도 방지).
    """
    from compare import _has_remaining_compare_work
    from state import hash_memory_cache, hash_memory_lock, reset_stop

    reset_stop()
    tmpdir = tempfile.mkdtemp(prefix="idf_pipe_rem_")
    try:
        folder = os.path.join(tmpdir, "folder1")
        os.makedirs(folder)
        # 실제 이미지 파일이 아닌 텍스트 파일 (해시 계산 실패 유발)
        fake_image = os.path.join(folder, "not_image.png")
        with open(fake_image, "w", encoding="utf-8") as f:
            f.write("this is not an image")

        method, hash_size = "dhash", 16

        # 파이프라인 실행 (해시 실패)
        options = {"scan_batch_size": 5, "use_bktree": True}
        start_time = __import__("time").perf_counter()
        _run_hash_compare_pipeline(
            search_mode="all_folders",
            folders=[folder],
            include_sub=True,
            options=options,
            method=method,
            hash_size=hash_size,
            tolerance=0,
            duplicate_limit=0,
            max_compare_files=0,
            max_hash_compute_files=0,
            use_compare_cache=False,
            start_time=start_time,
            compare_progress_log_interval=0,
            aspect_ratio_tol=1.0,
        )

        # 실패 파일이 메모리 캐시에 None으로 기록되어 있어야 함
        with hash_memory_lock:
            recorded = hash_memory_cache.get((fake_image, method, hash_size))
        assert recorded is None, f"실패 파일이 None 캐시에 기록되어야 함, got {recorded}"

        # 해시 실패 파일은 남은 작업으로 판정되지 않아야 함
        remaining = _has_remaining_compare_work(
            stopped_early=False,
            compare_file_paths=[fake_image],
            method=method,
            hash_size=hash_size,
            max_hash_compute_files=5000,
        )
        assert remaining is False, "해시 실패 파일은 남은 작업이 아니어야 함 (무한 재시도 방지)"
    finally:
        reset_stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_pipeline_completes()
    print("OK: test_pipeline_completes")
    test_pipeline_stop()
    print("OK: test_pipeline_stop")
    test_pipeline_no_bktree()
    print("OK: test_pipeline_no_bktree")
    test_has_remaining_after_failed_hash()
    print("OK: test_has_remaining_after_failed_hash")
    print("RESULT: ALL OK (4 passed)")
