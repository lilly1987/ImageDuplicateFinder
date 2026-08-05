"""
cache.db 정리 스크립트.

과대 해시 크기(128/256)로 인해 비대해진 테이블을 삭제하고 VACUUM 실행.
- hash_size^2 비트 해시(imagehash 라이브러리 특성)가 DB를 수십 GB로 만든 원인 데이터 제거
- 삭제 대상: hash_cache_dhash_128, hash_cache_dhash_256 및 관련 캐시/결과 테이블
- VACUUM으로 물리 파일 크기 즉시 축소

사용법: python cleanup_cache_db.py
주의: cache.db 백업 후 실행 권장. 앱이 종료된 상태에서 실행.
"""

import sqlite3
import os
import time

DB_FILE = "cache.db"
DB_TIMEOUT = 120  # VACUUM은 오래 걸리므로 충분히 대기

# 삭제 대상 해시 크기 (128, 256 = 과대 해시)
TARGET_SIZES = (128, 256)


def main():
    if not os.path.exists(DB_FILE):
        print(f"[오류] {DB_FILE} 파일이 없습니다.")
        return

    size_before = os.path.getsize(DB_FILE)
    print(f"현재 cache.db 크기: {size_before / 1024**3:.2f} GB")

    conn = sqlite3.connect(DB_FILE, timeout=DB_TIMEOUT)
    conn.execute("PRAGMA busy_timeout=120000")
    # WAL 모드 유지
    conn.execute("PRAGMA journal_mode=WAL")

    cur = conn.cursor()

    # 1. 삭제 대상 테이블 목록
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    all_tables = [r[0] for r in cur.fetchall()]

    drop_tables = []
    for prefix in ("hash_cache", "compare_cache", "compare_progress", "duplicate_results"):
        for size in TARGET_SIZES:
            name = f"{prefix}_dhash_{size}"
            if name in all_tables:
                drop_tables.append(name)

    if not drop_tables:
        print("삭제할 과대 해시 테이블이 없습니다.")
    else:
        print("\n[삭제 대상 테이블]")
        for t in drop_tables:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            cnt = cur.fetchone()[0]
            print(f"  - {t}: {cnt:,} 행")

        # 2. 행 수 미리 계산 후 삭제
        before_rows = {}
        for t in drop_tables:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            before_rows[t] = cur.fetchone()[0]

        print("\n테이블 삭제 중...")
        for t in drop_tables:
            cur.execute(f'DROP TABLE IF EXISTS "{t}"')
        conn.commit()
        print(f"삭제 완료: {len(drop_tables)}개 테이블, 총 {sum(before_rows.values()):,} 행")

    # 3. 남은 테이블 확인
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    remaining = [r[0] for r in cur.fetchall()]
    print(f"\n남은 테이블: {len(remaining)}개")
    for t in remaining:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        print(f"  - {t}: {cur.fetchone()[0]:,} 행")

    # 4. VACUUM으로 물리 크기 축소
    print("\nVACUUM 실행 중... (14GB 규모면 수 분 소요)")
    start = time.perf_counter()
    conn.execute("VACUUM")
    conn.commit()
    elapsed = time.perf_counter() - start

    # 5. 결과
    size_after = os.path.getsize(DB_FILE)
    saved = size_before - size_after
    print(f"\nVACUUM 완료: {elapsed:.1f}초")
    print(f"정리 전: {size_before / 1024**3:.2f} GB")
    print(f"정리 후: {size_after / 1024**3:.2f} GB")
    print(f"절감: {saved / 1024**3:.2f} GB ({(saved / size_before * 100) if size_before else 0:.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()