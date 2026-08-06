"""hasher._should_rehash 로직 단위 테스트"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hasher import _should_rehash


class FakeStat:
    """os.stat_result 대체 객체"""
    def __init__(self, st_mtime, st_size):
        self.st_mtime = st_mtime
        self.st_size = st_size


def _make_config(rehash_mtime=False, rehash_size=False):
    """config.load_config 모킹"""
    import config as config_module
    config_module.load_config = lambda: {
        "rehash_on_mtime_change": rehash_mtime,
        "rehash_on_size_change": rehash_size,
    }


def test_no_change_no_rehash():
    """mtime/size 모두 동일 → 항상 캐시 사용 (어떤 설정이든)"""
    _make_config(False, False)
    stat = FakeStat(100, 50)
    assert _should_rehash(100, 50, stat) is False

    _make_config(True, False)
    assert _should_rehash(100, 50, stat) is False

    _make_config(False, True)
    assert _should_rehash(100, 50, stat) is False

    _make_config(True, True)
    assert _should_rehash(100, 50, stat) is False


def test_default_both_unchecked():
    """기본 (둘 다 해제): mtime OR size 변경 시 재계산"""
    _make_config(False, False)
    assert _should_rehash(101, 50, FakeStat(100, 50)) is True   # mtime 변경
    assert _should_rehash(100, 51, FakeStat(100, 50)) is True   # size 변경
    assert _should_rehash(101, 51, FakeStat(100, 50)) is True   # 둘 다 변경


def test_mtime_only_checked():
    """mtime만 체크: mtime 변경 시만 재계산, size만 변경이면 캐시 유지"""
    _make_config(True, False)
    assert _should_rehash(101, 50, FakeStat(100, 50)) is True   # mtime 변경 → 재계산
    assert _should_rehash(100, 51, FakeStat(100, 50)) is False  # size만 변경 → 캐시 유지
    assert _should_rehash(101, 51, FakeStat(100, 50)) is True   # 둘 다 변경 → 재계산


def test_size_only_checked():
    """size만 체크: size 변경 시만 재계산, mtime만 변경이면 캐시 유지"""
    _make_config(False, True)
    assert _should_rehash(100, 51, FakeStat(100, 50)) is True   # size 변경 → 재계산
    assert _should_rehash(101, 50, FakeStat(100, 50)) is False  # mtime만 변경 → 캐시 유지
    assert _should_rehash(101, 51, FakeStat(100, 50)) is True   # 둘 다 변경 → 재계산


def test_both_checked():
    """둘 다 체크: mtime OR size 변경 시 재계산"""
    _make_config(True, True)
    assert _should_rehash(101, 50, FakeStat(100, 50)) is True   # mtime 변경
    assert _should_rehash(100, 51, FakeStat(100, 50)) is True   # size 변경
    assert _should_rehash(101, 51, FakeStat(100, 50)) is True   # 둘 다 변경
    assert _should_rehash(100, 50, FakeStat(100, 50)) is False  # 동일 → 캐시


def test_config_error_uses_default():
    """config 로드 실패 시 기본값 (둘 다 해제) → mtime OR size 변경 시 재계산"""
    import config as config_module
    original = config_module.load_config
    config_module.load_config = lambda: (_ for _ in ()).throw(RuntimeError("fail"))

    try:
        assert _should_rehash(101, 50, FakeStat(100, 50)) is True
        assert _should_rehash(100, 51, FakeStat(100, 50)) is True
        assert _should_rehash(100, 50, FakeStat(100, 50)) is False
    finally:
        config_module.load_config = original


if __name__ == "__main__":
    tests = [
        test_no_change_no_rehash,
