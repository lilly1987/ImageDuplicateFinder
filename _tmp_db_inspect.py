import sqlite3

conn = sqlite3.connect("file:cache.db?mode=ro", uri=True)
cur = conn.cursor()

# 1. 테이블 목록
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("테이블 목록:")
for t in tables:
    print("  -", t)
print()

# 2. 각 테이블 행 수 및 예상 크기
print("테이블별 행 수:")
for t in tables:
    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
    cnt = cur.fetchone()[0]
    try:
        cur.execute("SELECT SUM(pgsize) FROM dbstat WHERE name=?", (t,))
        row = cur.fetchone()
        usage = row[0] if row else 0
    except Exception:
        usage = 0
    print(f"  {t}: rows={cnt:,}  size~{usage/1024/1024:.1f} MB")
print()

# 3. hash_cache 테이블이 있는지 확인
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hash_cache%'")
hash_tables = [r[0] for r in cur.fetchall()]
print("hash_cache* 테이블:", hash_tables)

# 4. compare_cache 테이블 스키마
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'compare_cache%'")
compare_tables = [r[0] for r in cur.fetchall()]
print("compare_cache* 테이블:", compare_tables)
if compare_tables:
    cur.execute(f'SELECT sql FROM sqlite_master WHERE name=?', (compare_tables[0],))
    print("스키마 예시:", cur.fetchone()[0])

conn.close()