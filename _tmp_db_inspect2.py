import sqlite3

conn = sqlite3.connect("file:cache.db?mode=ro", uri=True)
cur = conn.cursor()

# 1. freelist 상태 (삭제된 페이지 수)
cur.execute("PRAGMA freelist_count")
freelist = cur.fetchone()[0]
cur.execute("PRAGMA page_count")
page_count = cur.fetchone()[0]
cur.execute("PRAGMA page_size")
page_size = cur.fetchone()[0]
print(f"page_count={page_count:,}, freelist_count={freelist:,}, page_size={page_size}")
print(f"총 크기 = {page_count*page_size/1024**3:.2f} GB")
print(f"freelist(삭제됨) = {freelist*page_size/1024**3:.3f} GB")
print()

# 2. 10개 테이블의 실제 페이지 사용량 (dbstat)
cur.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY SUM(pgsize) DESC LIMIT 15")
rows = cur.fetchall()
print("dbstat 기준 상위 15개 테이블 (실제 점유 페이지):")
for name, total in rows:
    print(f"  {name}: {total/1024**3:.3f} GB ({total/1024/1024:.1f} MB)")
print()

# 3. dbstat 집계 확인
cur.execute("SELECT SUM(pgsize) FROM dbstat")
multi = cur.fetchone()
if multi:
    print(f"dbstat 전체 합계: {multi[0]/1024**3:.2f} GB")
else:
    print("dbstat 없음")

# 4. hash_cache_dhash_256 행 평균 길이 (기본 테이블 B-tree 저장 구조 확인)
cur.execute("SELECT AVG(LENGTH(path)), AVG(LENGTH(hash)), MIN(LENGTH(hash)), MAX(LENGTH(hash)) FROM hash_cache_dhash_256")
stats = cur.fetchone()
print(f"\nhash_cache_dhash_256: 평균 path={stats[0]:.1f}B, 해시 평균={stats[1]:.1f}B, 해시 min={stats[2]}, max={stats[3]}")

cur.execute("SELECT COUNT(*), SUM(LENGTH(path)), SUM(LENGTH(hash)) FROM hash_cache_dhash_256")
cnt, path_sum, hash_sum = cur.fetchone()
print(f"행수={cnt:,}, path 합계={path_sum/1024**3:.3f} GB, hash 합계={hash_sum/1024**3:.3f} GB")

# 5. hash_cache_dhash_128 통계
cur.execute("SELECT COUNT(*), SUM(LENGTH(path)), SUM(LENGTH(hash)) FROM hash_cache_dhash_128")
cnt, path_sum, hash_sum = cur.fetchone()
print(f"\nhash_cache_dhash_128: 행수={cnt:,}, path 합계={path_sum/1024**3:.3f} GB, hash 합계={hash_sum/1024**3:.3f} GB")

conn.close()