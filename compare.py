import glob
import os, sqlite3, time, threading, atexit, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, wait, FIRST_COMPLETED, ALL_COMPLETED, as_completed, TimeoutError
from PIL import Image
import imagehash
from config import load_config
from logger import logger
from bk_match import find_near_duplicates_from_list
try:
    import faiss_batch_phash as faiss_batch
except Exception:
    faiss_batch = None

DB_FILE = "cache.db"
db_lock = threading.Lock()
stop_event = threading.Event()
hash_cache = {}
hash_cache_lock = threading.Lock()
hash_process_pool = None

db_write_lock = threading.Lock()
db_write_event = threading.Event()
db_write_stop = threading.Event()
hash_write_queue = []
compare_write_queue = []
db_write_thread = None
DB_WRITE_FLUSH_INTERVAL = 2.0
DB_WRITE_BATCH_SIZE = 100

# 중복 결과 수집
duplicate_pairs = set()
duplicates_lock = threading.Lock()

# 증분 비교 재시작 지원: 이미 처리한 파일은 기준으로 유지하고, 새로 추가된 파일만 비교한다.
compare_progress_state = {}
compare_progress_lock = threading.Lock()


def _compare_progress_key(method, hash_size):
    return (method, int(hash_size))


def select_incremental_compare_targets(current_paths, previous_paths):
    current = [p for p in dict.fromkeys(current_paths) if p]
    if not previous_paths:
        return current, []
    previous_set = {p for p in previous_paths if p}
    baseline = [p for p in current if p in previous_set]
    new_files = [p for p in current if p not in previous_set]
    return new_files, baseline


def select_hash_precompute_targets(current_paths, previous_paths):
    new_files, _ = select_incremental_compare_targets(current_paths, previous_paths)
    return new_files


def get_processed_compare_files(method, hash_size):
    key = _compare_progress_key(method, hash_size)
    with compare_progress_lock:
        return list(compare_progress_state.get(key, set()))


def update_processed_compare_files(method, hash_size, paths):
    key = _compare_progress_key(method, hash_size)
    with compare_progress_lock:
        bucket = compare_progress_state.setdefault(key, set())
        bucket.update(path for path in paths if path)


def record_duplicate_pair(f1, f2):
    with duplicates_lock:
        pair = tuple(sorted((f1, f2)))
        duplicate_pairs.add(pair)


def build_groups(pairs):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)

    groups = {}
    for x in parent:
        r = find(x)
        groups.setdefault(r, set()).add(x)

    # include any nodes that might not be in parent yet
    for a, b in pairs:
        if a not in parent:
            groups.setdefault(a, set()).add(a)
        if b not in parent:
            groups.setdefault(b, set()).add(b)

    return list(groups.values())


def _search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance_rate):
    ratio_str = str(round(aspect_ratio_tol, 4)).replace('.', 'p')
    tol_str = str(round(tolerance_rate, 4)).replace('.', 'p')
    return f"{method}_h{hash_size}_ratio{ratio_str}_tol{tol_str}"


def duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate):
    return f"duplicate_results_{_search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance_rate)}.json"


def resolve_search_options(method=None, hash_size=None, aspect_ratio_tol=None, tolerance_rate=None):
    if method is None or hash_size is None or aspect_ratio_tol is None or tolerance_rate is None:
        options = load_config()
        method = method if method is not None else options.get("compare_method", "ahash")
        hash_size = hash_size if hash_size is not None else int(options.get("hash_size", 8))
        aspect_ratio_tol = aspect_ratio_tol if aspect_ratio_tol is not None else float(options.get("aspect_ratio_tolerance", 0.02))
        tolerance_rate = tolerance_rate if tolerance_rate is not None else float(options.get("tolerance_rate", 0.05))
    return method, int(hash_size), float(aspect_ratio_tol), float(tolerance_rate)


def format_result_filename(method, hash_size, aspect_ratio_tol, tolerance_rate):
    return f"result_{_search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance_rate)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"


def write_result_file_if_any(method, hash_size, aspect_ratio_tol, tolerance_rate):
    with duplicates_lock:
        pairs = set(duplicate_pairs)
    if not pairs:
        return None
    groups = build_groups(pairs)
    fn = format_result_filename(method, hash_size, aspect_ratio_tol, tolerance_rate)
    path = os.path.join(os.getcwd(), fn)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"Search Options: method={method}, hash_size={hash_size}, aspect_ratio_tol={aspect_ratio_tol}, tolerance_rate={tolerance_rate}\n")
            fh.write("\n")
            for i, g in enumerate(groups, start=1):
                fh.write(f"Group {i}:\n")
                for p in sorted(g):
                    fh.write(p + "\n")
                fh.write("\n")
        return path
    except Exception:
        return None


def save_duplicate_results_json(method=None, hash_size=None, aspect_ratio_tol=None, tolerance_rate=None):
    method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options(
        method, hash_size, aspect_ratio_tol, tolerance_rate
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
            "tolerance_rate": tolerance_rate,
        },
        "groups": [sorted(list(g)) for g in groups],
    }
    path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _parse_duplicate_text_file(path):
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


def _load_groups_from_json(path, method, hash_size, aspect_ratio_tol, tolerance_rate):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    saved_options = data.get("search_options")
    if saved_options:
        if (
            saved_options.get("method") != method
            or int(saved_options.get("hash_size", -1)) != hash_size
            or round(float(saved_options.get("aspect_ratio_tol", -1)), 4) != round(aspect_ratio_tol, 4)
            or round(float(saved_options.get("tolerance_rate", -1)), 4) != round(tolerance_rate, 4)
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


def load_duplicate_results_json(method=None, hash_size=None, aspect_ratio_tol=None, tolerance_rate=None):
    method, hash_size, aspect_ratio_tol, tolerance_rate = resolve_search_options(
        method, hash_size, aspect_ratio_tol, tolerance_rate
    )
    json_path = duplicate_results_json_path(method, hash_size, aspect_ratio_tol, tolerance_rate)
    if os.path.exists(json_path):
        try:
            groups = _load_groups_from_json(json_path, method, hash_size, aspect_ratio_tol, tolerance_rate)
            if groups is not None:
                return groups
        except Exception:
            pass

    suffix = _search_options_suffix(method, hash_size, aspect_ratio_tol, tolerance_rate)
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


def get_duplicate_groups():
    with duplicates_lock:
        return build_groups(set(duplicate_pairs))


def start_db_writer():
    global db_write_thread
    if db_write_thread is None:
        db_write_thread = threading.Thread(target=_db_writer_loop, daemon=True)
        db_write_thread.start()


def _db_writer_loop():
    while not db_write_stop.is_set():
        db_write_event.wait(timeout=DB_WRITE_FLUSH_INTERVAL)
        db_write_event.clear()
        _flush_db_writes()
    _flush_db_writes()


def _flush_db_writes():
    with db_write_lock:
        hash_rows = list(hash_write_queue)
        compare_rows = list(compare_write_queue)
        hash_write_queue.clear()
        compare_write_queue.clear()

    if not hash_rows and not compare_rows:
        return

    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            if hash_rows:
                cur.executemany(
                    "REPLACE INTO hash_cache (path, method, hash_size, hash, mtime, size) VALUES (?,?,?,?,?,?)",
                    hash_rows
                )
            if compare_rows:
                cur.executemany(
                    "REPLACE INTO compare_cache (file1, file2, method, hash_size, tolerance_rate) VALUES (?,?,?,?,?)",
                    compare_rows
                )
            conn.commit()
        finally:
            conn.close()


def schedule_hash_cache_write(path, method, hash_size, hash_text, mtime, size):
    with db_write_lock:
        hash_write_queue.append((path, method, hash_size, hash_text, mtime, size))
        if len(hash_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def schedule_compare_record(file1, file2, method, hash_size, tolerance_rate):
    with db_write_lock:
        compare_write_queue.append((file1, file2, method, hash_size, float(tolerance_rate)))
        if len(compare_write_queue) >= DB_WRITE_BATCH_SIZE:
            db_write_event.set()
    start_db_writer()


def stop_db_writer():
    db_write_stop.set()
    db_write_event.set()
    if db_write_thread is not None:
        db_write_thread.join(timeout=5)


atexit.register(stop_db_writer)


def get_hash_process_pool():
    global hash_process_pool
    if hash_process_pool is None:
        max_workers = min(32, max(1, (os.cpu_count() or 4)))
        hash_process_pool = ProcessPoolExecutor(max_workers=max_workers)
    return hash_process_pool


def compute_hash_worker(path, method, hash_size):
    try:
        img = Image.open(path)
        if method == "ahash":
            h = imagehash.average_hash(img, hash_size=hash_size)
        elif method == "phash":
            h = imagehash.phash(img, hash_size=hash_size)
        elif method == "dhash":
            h = imagehash.dhash(img, hash_size=hash_size)
        elif method == "whash":
            h = imagehash.whash(img, hash_size=hash_size)
        else:
            return None
        return str(h)
    except Exception:
        return None

def request_stop():
    stop_event.set()
    # Ensure DB writer wakes and flushes any queued writes immediately
    try:
        start_db_writer()
    except Exception:
        pass
    try:
        db_write_event.set()
    except Exception:
        pass

def reset_stop():
    stop_event.clear()
    with hash_cache_lock:
        hash_cache.clear()

def is_stop_requested():
    return stop_event.is_set()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS hash_cache (
                path TEXT,
                method TEXT,
                hash_size INTEGER,
                hash TEXT,
                mtime INTEGER,
                size INTEGER
                ,PRIMARY KEY (path, method, hash_size)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS compare_cache (
                file1 TEXT,
                file2 TEXT,
                method TEXT,
                hash_size INTEGER,
                tolerance_rate REAL,
                PRIMARY KEY (file1, file2, method, hash_size)
            )""")
            conn.commit()
        finally:
            conn.close()

# ------------------------------
# 해시 캐시 관리
# ------------------------------
def get_file_hash(path, method="ahash", hash_size=8):
    if not os.path.isfile(path):
        return None
    try:
        stat = os.stat(path)
    except Exception:
        return None

    # DB에서 해시 확인
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute("SELECT hash, mtime, size FROM hash_cache WHERE path=? AND method=? AND hash_size=?",
                        (path, method, hash_size))
            row = cur.fetchone()
        finally:
            conn.close()

    if row and row[1] == stat.st_mtime and row[2] == stat.st_size:
        try:
            return imagehash.hex_to_hash(row[0])
        except Exception:
            pass

    if is_stop_requested():
        return None

    process_pool = get_hash_process_pool()
    future = process_pool.submit(compute_hash_worker, path, method, hash_size)
    while True:
        try:
            hash_text = future.result(timeout=0.5)
            break
        except TimeoutError:
            if is_stop_requested():
                try:
                    future.cancel()
                except Exception:
                    pass
                return None
            continue
        except KeyboardInterrupt:
            request_stop()
            try:
                future.cancel()
            except Exception:
                pass
            return None

    if hash_text is None:
        return None

    try:
        h = imagehash.hex_to_hash(hash_text)
    except Exception:
        return None

    schedule_hash_cache_write(path, method, hash_size, hash_text, stat.st_mtime, stat.st_size)
    return h

# ------------------------------
# 비교 이력 캐시 관리
# ------------------------------
def make_pair_key(file1, file2):
    return tuple(sorted([file1, file2]))

def already_compared(file1, file2, method, hash_size):
    f1, f2 = make_pair_key(file1, file2)
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT tolerance_rate FROM compare_cache WHERE file1=? AND file2=? AND method=? AND hash_size=?",
                    (f1, f2, method, hash_size))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def add_compare_record(file1, file2, method, hash_size, tolerance_rate):
    f1, f2 = make_pair_key(file1, file2)
    schedule_compare_record(f1, f2, method, hash_size, tolerance_rate)

# ------------------------------
# 해시 메모리 캐시
# ------------------------------
def get_cached_file_hash(key):
    with hash_cache_lock:
        if key in hash_cache:
            return hash_cache[key]

    path, method, hash_size = key
    h = get_file_hash(path, method, hash_size)

    with hash_cache_lock:
        hash_cache[key] = h

    return h


def _query_cached_hashes(paths, method, hash_size):
    if not paths:
        return {}

    cached = {}
    chunk_size = 200
    for i in range(0, len(paths), chunk_size):
        chunk = paths[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        with db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT path, hash, mtime, size FROM hash_cache WHERE method=? AND hash_size=? AND path IN ({placeholders})",
                    (method, hash_size, *chunk)
                )
                rows = cur.fetchall()
            finally:
                conn.close()

        for path, hash_text, mtime, size in rows:
            if not os.path.exists(path):
                continue
            try:
                stat = os.stat(path)
            except Exception:
                continue
            if stat.st_mtime == mtime and stat.st_size == size:
                try:
                    cached[path] = imagehash.hex_to_hash(hash_text)
                except Exception:
                    pass

    return cached


def precompute_hashes(paths, method, hash_size, batch_size=1000, max_new_hashes=0, previous_paths=None):
    unique_paths = list(dict.fromkeys(paths))
    hashes = {}
    missing = []

    with hash_cache_lock:
        for path in unique_paths:
            key = (path, method, hash_size)
            if key in hash_cache:
                hashes[path] = hash_cache[key]
            else:
                missing.append(path)

    if previous_paths is not None:
        new_paths = select_hash_precompute_targets(unique_paths, previous_paths)
        missing = [p for p in missing if p in new_paths]
        logger.info(f"[bold cyan][알림] 증분 비교 기준으로 새 해시 계산 대상 {len(missing)}개만 선택했습니다.[/bold cyan]")

    if missing:
        db_hashes = _query_cached_hashes(missing, method, hash_size)
        with hash_cache_lock:
            for path, h in db_hashes.items():
                key = (path, method, hash_size)
                hash_cache[key] = h
                hashes[path] = h
        logger.info(f"[bold cyan][알림] DB에서 {len(db_hashes)}개의 해시를 가져왔습니다.[/bold cyan]")

        missing = [p for p in missing if p not in db_hashes]
        already_cached_count = len(unique_paths) - len(missing) - len(db_hashes)
        _, effective_target_count = _describe_hash_precompute_targets(len(unique_paths), len(missing), max_new_hashes)
        if max_new_hashes > 0:
            if len(missing) >= max_new_hashes:
                logger.info(f"[bold cyan][알림] 설정된 새 해시 계산 수: {max_new_hashes}개를 실행합니다.[/bold cyan]")
                missing = missing[:max_new_hashes]
            else:
                logger.info(f"[bold cyan][알림] 남은 신규 대상이 {len(missing)}개라서 모두 계산합니다.[/bold cyan]")
                missing = missing[:len(missing)]

        logger.info(
            f"[bold cyan][알림] 새 해시 계산 대상: {len(missing)}개 "
            f"(설정값={max_new_hashes if max_new_hashes > 0 else '무제한'}, 남은 신규 대상={len(missing)}개, 이미 계산된 건수={already_cached_count}, 실제 실행={effective_target_count})[/bold cyan]"
        )

        if missing:
            process_pool = get_hash_process_pool()
            chunk_size = max(1, int(batch_size))
            for start in range(0, len(missing), chunk_size):
                if is_stop_requested():
                    break

                chunk_start = time.perf_counter()
                batch_paths = missing[start:start + chunk_size]
                futures = {
                    process_pool.submit(compute_hash_worker, path, method, hash_size): path
                    for path in batch_paths
                }
                pending = set(futures)
                while pending:
                    if is_stop_requested():
                        for fut in pending:
                            try:
                                fut.cancel()
                            except Exception:
                                pass
                        break

                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    if not done:
                        continue

                    for future in done:
                        path = futures[future]
                        try:
                            hash_text = future.result()
                        except Exception:
                            hash_text = None

                        if hash_text is None:
                            with hash_cache_lock:
                                hash_cache[(path, method, hash_size)] = None
                            hashes[path] = None
                        else:
                            try:
                                h = imagehash.hex_to_hash(hash_text)
                            except Exception:
                                with hash_cache_lock:
                                    hash_cache[(path, method, hash_size)] = None
                                hashes[path] = None
                            else:
                                hashes[path] = h
                                with hash_cache_lock:
                                    hash_cache[(path, method, hash_size)] = h
                                try:
                                    stat = os.stat(path)
                                    schedule_hash_cache_write(path, method, hash_size, hash_text, stat.st_mtime, stat.st_size)
                                except Exception:
                                    pass

                        if is_stop_requested():
                            for fut in pending:
                                try:
                                    fut.cancel()
                                except Exception:
                                    pass
                            pending.clear()
                            break

                elapsed = time.perf_counter() - chunk_start
                logger.info(f"[bold cyan][알림] batch {start // chunk_size + 1} 완료: {len(batch_paths)}개, 소요 {elapsed:.2f}초[/bold cyan]")

    return hashes


def hash_to_int(hash_value):
    if hash_value is None:
        return None
    try:
        return int(str(hash_value), 16)
    except Exception:
        return None


def get_hash_blocks(hash_value, total_bits, num_blocks):
    int_hash = hash_to_int(hash_value)
    if int_hash is None:
        return None
    blocks = []
    block_size = (total_bits + num_blocks - 1) // num_blocks
    for block_index in range(num_blocks):
        shift = total_bits - (block_index + 1) * block_size
        if shift >= 0:
            block_value = (int_hash >> shift) & ((1 << block_size) - 1)
        else:
            block_value = int_hash & ((1 << (total_bits - block_index * block_size)) - 1)
        blocks.append(block_value)
    return tuple(blocks)


def build_block_index(paths, hashes, num_blocks, total_bits):
    index = {}
    for path in paths:
        blocks = get_hash_blocks(hashes.get(path), total_bits, num_blocks)
        if blocks is None:
            continue
        for block_idx, value in enumerate(blocks):
            index.setdefault((block_idx, value), []).append(path)
    return index


def collect_candidate_pairs(paths, block_index):
    candidates = {path: set() for path in paths}
    for bucket_paths in block_index.values():
        if len(bucket_paths) < 2:
            continue
        for i in range(len(bucket_paths)):
            p1 = bucket_paths[i]
            for p2 in bucket_paths[i + 1:]:
                candidates[p1].add(p2)
                candidates[p2].add(p1)
    return candidates


def filter_batch_candidates(file1, batch, candidates):
    if candidates is None:
        return batch
    file_candidates = candidates.get(file1)
    if not file_candidates:
        return []
    return [file2 for file2 in batch if file2 in file_candidates]


def get_hash_prefix_bits(hash_size):
    total_bits = hash_size * hash_size
    return min(16, max(4, total_bits // 4))


def hash_prefix_key(hash_value, prefix_bits):
    if hash_value is None:
        return None
    hex_str = str(hash_value)
    total_bits = len(hex_str) * 4
    if prefix_bits >= total_bits:
        return hex_str
    try:
        int_value = int(hex_str, 16)
    except Exception:
        return None
    return int_value >> (total_bits - prefix_bits)


def build_hash_buckets(paths, hashes, prefix_bits):
    buckets = {}
    for path in paths:
        prefix = hash_prefix_key(hashes.get(path), prefix_bits)
        if prefix is None:
            continue
        buckets.setdefault(prefix, []).append(path)
    return buckets


# ------------------------------
# 파일 비교 함수
# ------------------------------
def compare_files(file1, file2, method, hash_size, tolerance, verbose=False, use_compare_cache=False, hashes=None):
    if is_stop_requested():
        return 0, False
    item_start = time.perf_counter()
    tolerance_rate = None
    if use_compare_cache:
        tolerance_rate = already_compared(file1, file2, method, hash_size)
    if tolerance_rate is not None:
        item_elapsed = (time.perf_counter() - item_start) * 1000
        is_duplicate = tolerance_rate <= tolerance / (hash_size * hash_size)
        if is_duplicate:
            logger.info(f"  [bold green][유사 발견][/bold green] [cyan]{file1}[/cyan] == [cyan]{file2}[/cyan] (rate={tolerance_rate:.3f}, {item_elapsed:.2f}ms)")
        elif verbose:
            logger.info(f"  [dim][건별 비교][/dim] {os.path.basename(file1)} ↔ {os.path.basename(file2)}: [yellow]{item_elapsed:.2f}ms[/yellow] [dim](캐시됨)[/dim]")
        return item_elapsed, is_duplicate

    # 새 비교 → 해시값 얻기 (메모리/DB 캐시 사용)
    if hashes is not None:
        h1 = hashes.get(file1)
        if h1 is None:
            h1 = get_cached_file_hash((file1, method, hash_size))
        h2 = hashes.get(file2)
        if h2 is None:
            h2 = get_cached_file_hash((file2, method, hash_size))
    else:
        h1 = get_cached_file_hash((file1, method, hash_size))
        h2 = get_cached_file_hash((file2, method, hash_size))

    if h1 is None or h2 is None:
        item_elapsed = (time.perf_counter() - item_start) * 1000
        return item_elapsed, False

    diff = h1 - h2
    tolerance_rate = diff / (hash_size * hash_size)

    # 결과 저장
    if use_compare_cache:
        add_compare_record(file1, file2, method, hash_size, tolerance_rate)

    item_elapsed = (time.perf_counter() - item_start) * 1000
    # 중복 조건 만족 시 출력
    is_duplicate = diff <= tolerance
    if is_duplicate:
        logger.info(f"  [bold green][유사 발견][/bold green] [cyan]{file1}[/cyan] == [cyan]{file2}[/cyan] (diff={diff}, rate={tolerance_rate:.3f}, {item_elapsed:.2f}ms)")
        try:
            record_duplicate_pair(file1, file2)
        except Exception:
            pass
    elif verbose:
        logger.info(f"  [dim][건별 비교][/dim] {os.path.basename(file1)} ↔ {os.path.basename(file2)}: [yellow]{item_elapsed:.2f}ms[/yellow]")

    return item_elapsed, is_duplicate

# ------------------------------
# 파일 그룹 비교 함수
# ------------------------------
def compare_file_with_list(file1, file2_list, method, hash_size, tolerance, verbose=False, use_compare_cache=False, duplicate_limit=0, hashes=None):
    if is_stop_requested():
        return 0, 0

    if hashes is not None:
        h1 = hashes.get(file1)
        if h1 is None:
            h1 = get_cached_file_hash((file1, method, hash_size))
    else:
        h1 = get_cached_file_hash((file1, method, hash_size))
    if h1 is None:
        return 0, 0

    total = 0
    duplicates = 0
    for file2 in file2_list:
        if is_stop_requested():
            break

        if hashes is not None:
            h2 = hashes.get(file2)
            if h2 is None:
                h2 = get_cached_file_hash((file2, method, hash_size))
        else:
            h2 = get_cached_file_hash((file2, method, hash_size))
        if h2 is None:
            continue

        if use_compare_cache:
            cached_rate = already_compared(file1, file2, method, hash_size)
            if cached_rate is not None:
                is_duplicate = cached_rate <= tolerance / (hash_size * hash_size)
                total += 1
                if is_duplicate:
                    duplicates += 1
                    try:
                        record_duplicate_pair(file1, file2)
                    except Exception:
                        pass
                    if duplicate_limit > 0 and duplicates >= duplicate_limit:
                        request_stop()
                        break
                continue

        diff = h1 - h2
        tolerance_rate = diff / (hash_size * hash_size)
        if use_compare_cache:
            add_compare_record(file1, file2, method, hash_size, tolerance_rate)

        is_duplicate = diff <= tolerance
        total += 1
        if is_duplicate:
            duplicates += 1
            try:
                record_duplicate_pair(file1, file2)
            except Exception:
                pass
            if duplicate_limit > 0 and duplicates >= duplicate_limit:
                request_stop()
                break

    return total, duplicates

# ------------------------------
# 메인 비교 함수
# ------------------------------
def _has_remaining_compare_work(stopped_early, compare_file_paths, method, hash_size, max_hash_compute_files):
    if stopped_early:
        return True
    if max_hash_compute_files <= 0 or not compare_file_paths:
        return False
    db_hashes = _query_cached_hashes(compare_file_paths, method, hash_size)
    return any(os.path.isfile(p) and p not in db_hashes for p in compare_file_paths)


def _compare_result(total_duplicates, compare_file_paths, method, hash_size, max_hash_compute_files, stopped_early=False):
    return {
        "total_duplicates": total_duplicates,
        "has_remaining": _has_remaining_compare_work(
            stopped_early, compare_file_paths, method, hash_size, max_hash_compute_files
        ),
    }


def _resolve_compare_options(options):
    search_mode = options.get("search_mode", "all_folders")
    include_sub = options.get("include_subfolders", "include") == "include"
    method = options.get("compare_method", "ahash")
    hash_size = int(options.get("hash_size", 8))
    tolerance_rate = float(options.get("tolerance_rate", 0.05))
    tolerance = max(0, min(hash_size * hash_size, int(round(tolerance_rate * hash_size * hash_size))))
    duplicate_limit = options.get("duplicate_limit_count", 1000)
    try:
        duplicate_limit = int(duplicate_limit)
        if duplicate_limit < 0:
            duplicate_limit = 0
    except Exception:
        duplicate_limit = 0
    max_compare_files = options.get("max_compare_files", 0)
    try:
        max_compare_files = int(max_compare_files)
        if max_compare_files < 0:
            max_compare_files = 0
    except Exception:
        max_compare_files = 0
    max_hash_compute_files = options.get("max_hash_compute_files", 0)
    compare_progress_log_interval = options.get("compare_progress_log_interval", 0)
    try:
        compare_progress_log_interval = int(compare_progress_log_interval)
        if compare_progress_log_interval < 0:
            compare_progress_log_interval = 0
    except Exception:
        compare_progress_log_interval = 0
    try:
        max_hash_compute_files = int(max_hash_compute_files)
        if max_hash_compute_files < 0:
            max_hash_compute_files = 0
    except Exception:
        max_hash_compute_files = 0
    save_duplicate_results = bool(options.get("save_duplicate_results", False))
    load_saved_results = bool(options.get("load_saved_results_on_start", False))
    auto_open_duplicate_results = bool(options.get("auto_open_duplicate_results", False))
    use_compare_cache = bool(options.get("use_compare_cache", False))
    aspect_ratio_tol = float(options.get("aspect_ratio_tolerance", 0.02))
    batch_size = int(options.get("hash_precompute_batch_size", 1000))
    return {
        "search_mode": search_mode,
        "include_sub": include_sub,
        "method": method,
        "hash_size": hash_size,
        "tolerance_rate": tolerance_rate,
        "tolerance": tolerance,
        "duplicate_limit": duplicate_limit,
        "max_compare_files": max_compare_files,
        "max_hash_compute_files": max_hash_compute_files,
        "compare_progress_log_interval": compare_progress_log_interval,
        "save_duplicate_results": save_duplicate_results,
        "load_saved_results": load_saved_results,
        "auto_open_duplicate_results": auto_open_duplicate_results,
        "use_compare_cache": use_compare_cache,
        "aspect_ratio_tol": aspect_ratio_tol,
        "batch_size": batch_size,
    }


def _collect_files_for_mode(folders, include_sub, search_mode):
    if search_mode == "cross_folder":
        folder_files = {}
        all_target_files = []
        for folder in folders:
            files = []
            if include_sub:
                for root_dir, dirs, fs in os.walk(folder):
                    for file in fs:
                        full = os.path.join(root_dir, file)
                        if os.path.isfile(full):
                            files.append(full)
                            all_target_files.append(full)
            else:
                for file in os.listdir(folder):
                    full = os.path.join(folder, file)
                    if os.path.isfile(full):
                        files.append(full)
                        all_target_files.append(full)
            folder_files[folder] = files
        return folder_files, all_target_files

    all_files = []
    for folder in folders:
        if include_sub:
            for root_dir, dirs, fs in os.walk(folder):
                for file in fs:
                    full = os.path.join(root_dir, file)
                    if os.path.isfile(full):
                        all_files.append(full)
        else:
            for file in os.listdir(folder):
                full = os.path.join(folder, file)
                if os.path.isfile(full):
                    all_files.append(full)
    return None, all_files


def _apply_max_compare_files(search_mode, folder_files, all_files, max_compare_files):
    if max_compare_files <= 0:
        return folder_files, all_files
    if search_mode == "cross_folder":
        selected = []
        new_folder_files = {}
        for folder, files in folder_files.items():
            if len(selected) >= max_compare_files:
                new_folder_files[folder] = []
                continue
            take = max_compare_files - len(selected)
            selected_files = files[:take]
            selected.extend(selected_files)
            new_folder_files[folder] = selected_files
        return new_folder_files, selected
    return None, all_files[:max_compare_files]


def _prepare_incremental_targets(compare_file_paths, method, hash_size):
    processed_compare_files = get_processed_compare_files(method, hash_size)
    new_compare_files, baseline_compare_files = select_incremental_compare_targets(compare_file_paths, processed_compare_files)
    return set(new_compare_files), baseline_compare_files, new_compare_files


def _run_pre_scan_steps(all_target_files, method, hash_size, aspect_ratio_tol, duplicate_limit, total_duplicates):
    try:
        if faiss_batch and method in ("phash", "dhash"):
            logger.info("Running FAISS pre-scan for near-duplicates...")
            faiss_pairs = faiss_batch.build_and_search_from_paths(all_target_files, hash_size=hash_size, threshold=1)
            logger.info(f"FAISS pre-scan found {len(faiss_pairs)} candidate duplicate pairs")
            for a, b in faiss_pairs:
                record_duplicate_pair(a, b)
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    return total_duplicates, True
    except Exception:
        logger.debug("FAISS pre-scan failed or not available; continuing")

    try:
        if faiss_batch and method in ("phash", "dhash"):
            logger.info("Running FAISS pre-scan for near-duplicates...")
            faiss_pairs = faiss_batch.build_and_search_from_paths(all_target_files, hash_size=hash_size, threshold=1)
            logger.info(f"FAISS pre-scan found {len(faiss_pairs)} candidate duplicate pairs")
            for a, b in faiss_pairs:
                record_duplicate_pair(a, b)
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    return total_duplicates, True
    except Exception:
        logger.debug("FAISS pre-scan failed or not available; continuing")

    try:
        if method in ("phash", "dhash"):
            logger.info("Running BK-tree pre-scan for near-duplicates...")
            bk_dups = find_near_duplicates_from_list(all_target_files, threshold=1, ratio_tol=aspect_ratio_tol, method="phash" if method == "phash" else "dhash", hash_size=hash_size)
            logger.info(f"BK-tree pre-scan found {len(bk_dups)} candidate duplicate pairs")
            for a, b, dist in bk_dups:
                record_duplicate_pair(a, b)
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    return total_duplicates, True
    except Exception:
        logger.debug("BK-tree pre-scan failed; continuing with normal compare")

    try:
        if method in ("phash", "dhash"):
            logger.info("Running BK-tree pre-scan for near-duplicates...")
            bk_dups = find_near_duplicates_from_list(all_target_files, threshold=1, ratio_tol=aspect_ratio_tol, method="phash" if method == "phash" else "dhash", hash_size=hash_size)
            logger.info(f"BK-tree pre-scan found {len(bk_dups)} candidate duplicate pairs")
            for a, b, dist in bk_dups:
                def _top_folder(p):
                    for key in all_target_files:
                        if p == key:
                            return "root"
                    return None
                ta = _top_folder(a)
                tb = _top_folder(b)
                if ta is not None and tb is not None and ta != tb:
                    record_duplicate_pair(a, b)
                    total_duplicates += 1
                    if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                        logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                        request_stop()
                        return total_duplicates, True
    except Exception:
        logger.debug("BK-tree pre-scan failed; continuing with normal compare")

    return total_duplicates, False


def _estimate_compare_total_pairs(search_mode, folder_files, all_files, new_compare_files_set):
    if search_mode == "cross_folder":
        total_pairs = 0
        for i in range(len(folder_files)):
            keys = list(folder_files.keys())
            if i >= len(keys):
                break
            for j in range(i + 1, len(keys)):
                f1, f2 = keys[i], keys[j]
                pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
                if pending_files:
                    total_pairs += len(pending_files) * len(folder_files[f2])
        return total_pairs

    pending_files = [p for p in all_files if p in new_compare_files_set]
    return max(0, len(pending_files) * (len(all_files) - 1))


def _calculate_log_interval(total_pairs, configured_interval=None):
    if configured_interval is not None:
        try:
            configured_interval = int(configured_interval)
        except Exception:
            configured_interval = None
        if configured_interval is not None and configured_interval > 0:
            return configured_interval

    if total_pairs <= 0:
        return 1
    if total_pairs >= 50_000_000:
        return 100_000
    if total_pairs >= 5_000_000:
        return 10_000
    if total_pairs >= 500_000:
        return 5_000
    if total_pairs >= 50_000:
        return 1_000
    if total_pairs >= 5_000:
        return 1_000
    return max(1, total_pairs // 10)


def _accumulate_compare_progress(total_compared, completed_result):
    completed_pairs, is_duplicate = completed_result
    return total_compared + completed_pairs, is_duplicate


def _describe_hash_precompute_targets(total_candidates, remaining_new_targets, max_new_hashes):
    already_cached_count = max(0, total_candidates - remaining_new_targets)
    if max_new_hashes > 0:
        effective_target_count = min(remaining_new_targets, max_new_hashes)
    else:
        effective_target_count = remaining_new_targets
    return already_cached_count, effective_target_count


def _run_cross_folder_compare(folder_files, new_compare_files_set, hashes, candidates, method, hash_size, tolerance, duplicate_limit, use_compare_cache, start_time, log_interval, verbose_single):
    total_compared = 0
    total_duplicates = 0
    total_pairs = 0
    keys = list(folder_files.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            f1, f2 = keys[i], keys[j]
            pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
            if pending_files:
                total_pairs += len(pending_files) * len(folder_files[f2])

    last_log_time = time.perf_counter()
    last_log_count = 0
    max_workers = min(32, max(1, (os.cpu_count() or 4) + 4))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = set()

    def drain_futures(wait_all=False):
        nonlocal total_compared, total_duplicates, last_log_time, last_log_count
        if not futures:
            return False
        done, _ = wait(futures, return_when=ALL_COMPLETED if wait_all else FIRST_COMPLETED)
        stop_now = False
        for fut in done:
            futures.discard(fut)
            if fut.cancelled():
                continue
            try:
                completed_pairs, is_duplicate = fut.result()
            except Exception:
                continue
            total_compared, _ = _accumulate_compare_progress(total_compared, (completed_pairs, is_duplicate))
            if is_duplicate:
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    stop_now = True
            if total_compared % log_interval == 0 or total_compared == total_pairs:
                now = time.perf_counter()
                interval_time = now - last_log_time
                interval_count = total_compared - last_log_count
                total_elapsed = now - start_time
                speed = interval_count / interval_time if interval_time > 0 else 0
                percent = (total_compared / total_pairs * 100) if total_pairs > 0 else 100.0
                logger.info(
                    f"[bold cyan][진행 상황][/bold cyan] "
                    f"[yellow]{total_compared:,}[/yellow] / {total_pairs:,}회 ({percent:.1f}%) | "
                    f"중복 건수: {total_duplicates:,} | "
                    f"최근 {interval_count:,}개: {interval_time:.2f}초 ({speed:.0f}개/초) | "
                    f"전체 경과: {total_elapsed:.1f}초"
                )
                last_log_time = now
                last_log_count = total_compared
        return stop_now

    try:
        for i in range(len(keys)):
            if is_stop_requested():
                break
            for j in range(i + 1, len(keys)):
                if is_stop_requested():
                    break
                f1, f2 = keys[i], keys[j]
                pending_files = [p for p in folder_files[f1] if p in new_compare_files_set]
                for file1 in pending_files:
                    update_processed_compare_files(method, hash_size, [file1])
                    if is_stop_requested():
                        break
                    batch = []
                    for file2 in folder_files[f2]:
                        if is_stop_requested():
                            break
                        batch.append(file2)
                        if len(batch) >= max_workers * 4:
                            filtered = filter_batch_candidates(file1, batch, candidates)
                            if filtered:
                                futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                            batch = []
                    if batch:
                        filtered = filter_batch_candidates(file1, batch, candidates)
                        if filtered:
                            futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                    if len(futures) >= max_workers * 2 and drain_futures():
                        break
                if is_stop_requested():
                    break
            if is_stop_requested():
                break
        if not is_stop_requested():
            drain_futures(wait_all=True)
    finally:
        for fut in futures:
            fut.cancel()
        executor.shutdown(wait=False)

    return total_compared, total_duplicates, total_pairs


def _run_all_folder_compare(all_files, new_compare_files_set, hashes, candidates, method, hash_size, tolerance, duplicate_limit, use_compare_cache, start_time, log_interval, verbose_single):
    total_compared = 0
    total_duplicates = 0
    total_pairs = max(0, len([p for p in all_files if p in new_compare_files_set]) * (len(all_files) - 1))
    last_log_time = time.perf_counter()
    last_log_count = 0
    max_workers = min(32, max(1, (os.cpu_count() or 4) + 4))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = set()

    def drain_futures(wait_all=False):
        nonlocal total_compared, total_duplicates, last_log_time, last_log_count
        if not futures:
            return False
        done, _ = wait(futures, return_when=ALL_COMPLETED if wait_all else FIRST_COMPLETED)
        stop_now = False
        for fut in done:
            futures.discard(fut)
            if fut.cancelled():
                continue
            try:
                completed_pairs, is_duplicate = fut.result()
            except Exception:
                continue
            total_compared, _ = _accumulate_compare_progress(total_compared, (completed_pairs, is_duplicate))
            if is_duplicate:
                total_duplicates += 1
                if duplicate_limit > 0 and total_duplicates >= duplicate_limit:
                    logger.warning(f"[bold yellow][중단][/bold yellow] 중복 {total_duplicates:,}건 도달로 비교를 중단합니다.")
                    request_stop()
                    stop_now = True
            if total_compared % log_interval == 0 or total_compared == total_pairs:
                now = time.perf_counter()
                interval_time = now - last_log_time
                interval_count = total_compared - last_log_count
                total_elapsed = now - start_time
                speed = interval_count / interval_time if interval_time > 0 else 0
                percent = (total_compared / total_pairs * 100) if total_pairs > 0 else 100.0
                logger.info(
                    f"[bold cyan][진행 상황][/bold cyan] "
                    f"[yellow]{total_compared:,}[/yellow] / {total_pairs:,}회 ({percent:.1f}%) | "
                    f"중복 건수: {total_duplicates:,} | "
                    f"최근 {interval_count:,}개: {interval_time:.2f}초 ({speed:.0f}개/초) | "
                    f"전체 경과: {total_elapsed:.1f}초"
                )
                last_log_time = now
                last_log_count = total_compared
        return stop_now

    try:
        for file1 in [p for p in all_files if p in new_compare_files_set]:
            if is_stop_requested():
                break
            update_processed_compare_files(method, hash_size, [file1])
            batch = []
            for file2 in all_files:
                if file2 == file1:
                    continue
                if is_stop_requested():
                    break
                batch.append(file2)
                if len(batch) >= max_workers * 4:
                    filtered = filter_batch_candidates(file1, batch, candidates)
                    if filtered:
                        futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
                    batch = []
            if batch:
                filtered = filter_batch_candidates(file1, batch, candidates)
                if filtered:
                    futures.add(executor.submit(compare_file_with_list, file1, filtered, method, hash_size, tolerance, verbose=verbose_single, use_compare_cache=use_compare_cache, duplicate_limit=duplicate_limit, hashes=hashes))
            if len(futures) >= max_workers * 2 and drain_futures():
                break
        if not is_stop_requested():
            drain_futures(wait_all=True)
    finally:
        for fut in futures:
            fut.cancel()
        executor.shutdown(wait=False)

    return total_compared, total_duplicates, total_pairs


def _run_compare_branch(search_mode, folders, include_sub, options, method, hash_size, tolerance, duplicate_limit, max_compare_files, max_hash_compute_files, use_compare_cache, start_time, aspect_ratio_tol, tolerance_rate, compare_progress_log_interval=0):
    total_compared = 0
    total_duplicates = 0
    total_pairs = 0
    compare_file_paths = []

    if search_mode == "cross_folder":
        logger.info("[bold cyan]cross_folder 모드[/bold cyan]")
        folder_files, all_target_files = _collect_files_for_mode(folders, include_sub, search_mode)
        logger.info(f"total={len(all_target_files)}")
        folder_files, all_target_files = _apply_max_compare_files(search_mode, folder_files, all_target_files, max_compare_files)
        compare_file_paths = list(all_target_files)
        new_compare_files_set, baseline_compare_files, new_compare_files = _prepare_incremental_targets(compare_file_paths, method, hash_size)
        if baseline_compare_files:
            logger.info(f"[bold cyan][알림] 증분 비교 모드: 기준 파일 {len(baseline_compare_files)}개, 신규 파일 {len(new_compare_files)}개[/bold cyan]")
        elif new_compare_files:
            logger.info(f"[bold cyan][알림] 신규 파일 {len(new_compare_files)}개를 기준으로 비교를 시작합니다.[/bold cyan]")
        else:
            logger.info("[bold cyan][알림] 새로 추가된 비교 대상이 없어 비교를 건너뜁니다.[/bold cyan]")
            return 0, total_duplicates, 0, compare_file_paths

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        logger.info(f"Precomputing hashes for {len(all_target_files)} files...")
        hashes = precompute_hashes(
            all_target_files,
            method,
            hash_size,
            batch_size=options.get("hash_precompute_batch_size", 1000),
            max_new_hashes=max_hash_compute_files,
            previous_paths=get_processed_compare_files(method, hash_size),
        )
        valid = sum(1 for v in hashes.values() if v)
        none_count = sum(1 for v in hashes.values() if v is None)
        missing_count = len(all_target_files) - len(hashes)
        logger.info(f"Hashes precomputed: entries={len(hashes)}, valid={valid}, none={none_count}, missing={missing_count}")

        total_duplicates, stopped = _run_pre_scan_steps(all_target_files, method, hash_size, aspect_ratio_tol, duplicate_limit, total_duplicates)
        if stopped:
            return 0, total_duplicates, 0, compare_file_paths

        prefix_bits = get_hash_prefix_bits(hash_size)
        bucket_index = build_hash_buckets(all_target_files, hashes, prefix_bits)
        bucket_count = len(bucket_index)
        max_bucket = max((len(v) for v in bucket_index.values()), default=0)
        logger.info(f"Built {bucket_count} buckets (prefix_bits={prefix_bits}), max_bucket_size={max_bucket}")
        candidates = collect_candidate_pairs(all_target_files, bucket_index)

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        estimated_total_pairs = _estimate_compare_total_pairs(search_mode, folder_files, all_target_files, new_compare_files_set)
        log_interval = _calculate_log_interval(estimated_total_pairs, compare_progress_log_interval)
        logger.info(f"Progress log interval: {log_interval} pairs (estimated_total_pairs={estimated_total_pairs:,})")
        total_compared, total_duplicates, total_pairs = _run_cross_folder_compare(
            folder_files,
            new_compare_files_set,
            hashes,
            candidates,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            use_compare_cache,
            start_time,
            log_interval,
            estimated_total_pairs < 50,
        )
    else:
        logger.info("[bold cyan][all_folders 모드][/bold cyan]")
        _, all_files = _collect_files_for_mode(folders, include_sub, search_mode)
        _, all_files = _apply_max_compare_files(search_mode, None, all_files, max_compare_files)
        compare_file_paths = list(all_files)
        new_compare_files_set, baseline_compare_files, new_compare_files = _prepare_incremental_targets(compare_file_paths, method, hash_size)
        if baseline_compare_files:
            logger.info(f"[bold cyan][알림] 증분 비교 모드: 기준 파일 {len(baseline_compare_files)}개, 신규 파일 {len(new_compare_files)}개[/bold cyan]")
        elif new_compare_files:
            logger.info(f"[bold cyan][알림] 신규 파일 {len(new_compare_files)}개를 기준으로 비교를 시작합니다.[/bold cyan]")
        else:
            logger.info("[bold cyan][알림] 새로 추가된 비교 대상이 없어 비교를 건너뜁니다.[/bold cyan]")
            return 0, total_duplicates, 0, compare_file_paths

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        logger.info(f"Precomputing hashes for {len(all_files)} files...")
        hashes = precompute_hashes(
            all_files,
            method,
            hash_size,
            batch_size=options.get("hash_precompute_batch_size", 1000),
            max_new_hashes=max_hash_compute_files,
            previous_paths=get_processed_compare_files(method, hash_size),
        )
        valid = sum(1 for v in hashes.values() if v)
        none_count = sum(1 for v in hashes.values() if v is None)
        missing_count = len(all_files) - len(hashes)
        logger.info(f"Hashes precomputed: entries={len(hashes)}, valid={valid}, none={none_count}, missing={missing_count}")

        prefix_bits = get_hash_prefix_bits(hash_size)
        bucket_index = build_hash_buckets(all_files, hashes, prefix_bits)
        bucket_count = len(bucket_index)
        max_bucket = max((len(v) for v in bucket_index.values()), default=0)
        logger.info(f"Built {bucket_count} buckets (prefix_bits={prefix_bits}), max_bucket_size={max_bucket}")
        candidates = collect_candidate_pairs(all_files, bucket_index)

        if is_stop_requested():
            logger.warning("[bold yellow][비교 중단됨][/bold yellow]")
            return 0, total_duplicates, 0, compare_file_paths

        n = len(all_files)
        pending_files = [p for p in all_files if p in new_compare_files_set]
        total_pairs = max(0, len(pending_files) * (n - 1))
        logger.info(f"Starting all_folders compare: total_pairs={total_pairs}, files={n}, pending_files={len(pending_files)}")
        log_interval = _calculate_log_interval(total_pairs, compare_progress_log_interval)
        logger.info(f"Progress log interval: {log_interval} pairs (estimated_total_pairs={total_pairs:,})")
        verbose_single = total_pairs < 50
        total_compared, total_duplicates, total_pairs = _run_all_folder_compare(
            all_files,
            new_compare_files_set,
            hashes,
            candidates,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            use_compare_cache,
            start_time,
            log_interval,
            verbose_single,
        )

    return total_compared, total_duplicates, total_pairs, compare_file_paths


def try_compare(folder_list):
    reset_stop()
    start_time = time.perf_counter()
    logger.info("[bold yellow][비교 시작][/bold yellow]")
    options = load_config()
    compare_options = _resolve_compare_options(options)
    search_mode = compare_options["search_mode"]
    include_sub = compare_options["include_sub"]
    method = compare_options["method"]
    hash_size = compare_options["hash_size"]
    tolerance_rate = compare_options["tolerance_rate"]
    tolerance = compare_options["tolerance"]
    duplicate_limit = compare_options["duplicate_limit"]
    max_compare_files = compare_options["max_compare_files"]
    max_hash_compute_files = compare_options["max_hash_compute_files"]
    compare_progress_log_interval = compare_options["compare_progress_log_interval"]
    save_duplicate_results = compare_options["save_duplicate_results"]
    use_compare_cache = compare_options["use_compare_cache"]
    aspect_ratio_tol = compare_options["aspect_ratio_tol"]
    logger.info(f"Options: mode={search_mode}, include_sub={include_sub}, method={method}, hash_size={hash_size}, tolerance={tolerance} ({tolerance_rate}), duplicate_limit={duplicate_limit}, max_compare_files={max_compare_files}, max_hash_compute_files={max_hash_compute_files}, progress_log_interval={compare_progress_log_interval}, save_results={save_duplicate_results}, load_saved_results={compare_options['load_saved_results']}, auto_open={compare_options['auto_open_duplicate_results']}, use_compare_cache={use_compare_cache}")

    if compare_options["load_saved_results"]:
        groups = load_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
        if groups:
            logger.info(f"[bold cyan][알림] 이전 저장된 중복 결과 {len(groups)}개 그룹을 불러왔습니다.")

    folders = [entry.split(": ", 1)[1] for entry in folder_list.get(0, "end")]
    init_db()

    try:
        total_compared, total_duplicates, total_pairs, compare_file_paths = _run_compare_branch(
            search_mode,
            folders,
            include_sub,
            options,
            method,
            hash_size,
            tolerance,
            duplicate_limit,
            max_compare_files,
            max_hash_compute_files,
            use_compare_cache,
            start_time,
            aspect_ratio_tol,
            tolerance_rate,
            compare_progress_log_interval,
        )

        elapsed_time = time.perf_counter() - start_time
        if is_stop_requested():
            logger.warning(f"[bold yellow][비교 중단됨][/bold yellow] 사용자에 의해 중단되었습니다. (진행: {total_compared:,} / {total_pairs:,}회, 소요 시간: {elapsed_time:.2f}초)")
        result_path = None
        json_path = None
        try:
            result_path = write_result_file_if_any(method, hash_size, aspect_ratio_tol, tolerance_rate)
            if result_path:
                logger.info(f"[bold green][결과 저장][/bold green] {result_path}")
        except Exception:
            pass

        try:
            json_path = save_duplicate_results_json(method, hash_size, aspect_ratio_tol, tolerance_rate)
            if json_path and save_duplicate_results:
                logger.info(f"[bold green][JSON 저장][/bold green] {json_path}")
        except Exception:
            pass

        if json_path:
            logger.info(f"[bold green][비교 완료][/bold green] 소요 시간: {elapsed_time:.2f}초 (총 {total_compared:,}회 비교, JSON 저장: {json_path})")
        else:
            logger.info(f"[bold green][비교 완료][/bold green] 소요 시간: {elapsed_time:.2f}초 (총 {total_compared:,}회 비교)")
        return _compare_result(
            total_duplicates,
            compare_file_paths,
            method,
            hash_size,
            max_hash_compute_files,
            stopped_early=is_stop_requested(),
        )

    except Exception as e:
        elapsed_time = time.perf_counter() - start_time
        logger.error(f"[bold red][에러 발생][/bold red] (소요 시간: {elapsed_time:.2f}초) {e}", exc_info=True)
        raise
    finally:
        _flush_db_writes()
