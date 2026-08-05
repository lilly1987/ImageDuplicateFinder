import sqlite3

conn = sqlite3.connect("file:cache.db?mode=ro", uri=True)
cur = conn.cursor()

# 1. hash_cache_dhash_256: 해시 길이 통계
print("=== hash_cache_dhash_256 해시/경로 길이 통계 ===")
cur.execute("SELECT MIN(LENGTH(hash)), MAX(LENGTH(hash)), AVG(LENGTH(hash)), COUNT(*) FROM hash_cache_dhash_256")
hmin, hmax, havg, cnt = cur.fetchone()
print(f"hash: min={hmin}, max={hmax}, avg={havg:.1f}, rows={cnt:,}")

cur.execute("SELECT MIN(LENGTH(path)), MAX(LENGTH(path)), AVG(LENGTH(path)) FROM hash_cache_dhash_256")
pmin, pmax, pavg = cur.fetchone()
print(f"path: min={pmin}, max={pmax}, avg={pavg:.1f}")

# 2. 가장 긴 해시 5개 샘플
print("\n가장 긴 해시 5개 (처음 80자):")
cur.execute("SELECT path, hash, LENGTH(hash) FROM hash_cache_dhash_256 ORDER BY LENGTH(hash) DESC LIMIT 5")
for path, h, l in cur.fetchall():
    print(f"  len={l}: hash={h[:80]}... path={path[:120]}")

# 3. 가장 긴 경로 5개 샘플
print("\n가장 긴 경로 5개:")
cur.execute("SELECT path, LENGTH(path) FROM hash_cache_dhash_256 ORDER BY LENGTH(path) DESC LIMIT 5")
for path, l in cur.fetchall():
    print(f"  len={l}: {path[:150]}")

# 4. 이 테이블 데이터 합계 (path+hash+mtime+size+오버헤드 근사)
cur.execute("SELECT SUM(LENGTH(path)+LENGTH(hash)+17) FROM hash_cache_dhash_256")
total = cur.fetchone()[0]
print(f"\nhash_cache_dhash_256 데이터 바이트 합계: {total/1024**3:.3f} GB")

# 5. 전체 hash_cache 테이블 데이터 합계
print("\n=== 전체 hash_cache* 테이블 데이터 합계 ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hash_cache%'")
hash_tables = [r[0] for r in cur.fetchall()]
grand_payload = 0
for t in hash_tables:
    try:
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(LENGTH(path)+LENGTH(hash)+17),0) FROM \"{t}\"")
        cnt, payload = cur.fetchone()
        grand_payload += payload
        print(f"  {t}: rows={cnt:,}, payload={payload/1024**3:.3f} GB")
    except Exception as e:
        print(f"  {t}: 오류 {e}")
print(f"\nhash_cache 전체 payload 합계: {grand_payload/1024**3:.3f} GB")

# 6. 전체 테이블 (hash, compare, progress, duplicate) payload 합계
print("\n=== 모든 테이블 payload 합계 (path/hash 문자열 기준) ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
all_tables = [r[0] for r in cur.fetchall()]
total_payload = 0
for t in all_tables:
    try:
        if t.startswith("compare_cache"):
            cols = "LENGTH(file1)+LENGTH(file2)+5"
        elif t.startswith("compare_progress"):
            cols = "LENGTH(path)+5"
        elif t.startswith("duplicate_results"):
            cols = "LENGTH(path)+12"
        elif t.startswith("hash_cache"):
            cols = "LENGTH(path)+LENGTH(hash)+17"
        else:
            continue
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM({cols}),0) FROM \"{t}\"")
        cnt, payload = cur.fetchone()
        total_payload += payload
        if payload > 50 * 1024 * 1024:  # 50MB 이상만
            print(f"  {t}: rows={cnt:,}, payload={payload/1024**3:.3f} GB")
    except Exception:
        pass
print(f"\n전체 payload 합계: {total_payload/1024**3:.2f} GB")

conn.close()