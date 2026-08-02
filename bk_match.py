import os
import math
import logging
from collections import defaultdict
from PIL import Image
import imagehash

logger = logging.getLogger(__name__)


def _hash_to_int(h):
    try:
        return int(str(h), 16)
    except Exception:
        try:
            arr = h.hash.flatten()
            val = 0
            for bit in arr:
                val = (val << 1) | int(bool(bit))
            return val
        except Exception:
            return int(str(h), 16)


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class BKNode:
    def __init__(self, key_int, path):
        self.key = key_int
        self.paths = [path]
        self.children = {}


class BKTree:
    def __init__(self):
        self.root = None

    def insert(self, key_int, path):
        if self.root is None:
            self.root = BKNode(key_int, path)
            return
        node = self.root
        while True:
            d = _hamming(key_int, node.key)
            if d == 0:
                node.paths.append(path)
                return
            child = node.children.get(d)
            if child is None:
                node.children[d] = BKNode(key_int, path)
                return
            node = child

    def query(self, key_int, max_dist):
        if self.root is None:
            return []
        results = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = _hamming(key_int, node.key)
            if d <= max_dist:
                for p in node.paths:
                    results.append((p, d))
            low = d - max_dist
            high = d + max_dist
            for dist, child in node.children.items():
                if low <= dist <= high:
                    stack.append(child)
        return results


def find_near_duplicates(
    image_dir,
    threshold=1,
    ratio_tol=0.02,
    size_bucket_mb=0.5,
    method="phash",
    hash_size=8,
    exts=None,
):
    """
    Fast near-duplicate finder using two-stage filtering + BK-tree on 64-bit hashes.

    - Stage 1: bucket by aspect ratio (rounded to `ratio_tol`) and file size (MB buckets).
    - Stage 2: within each bucket, index hashes in a BK-tree and query by Hamming distance <= `threshold`.

    Returns list of duplicate pairs: (file_path, matched_path)
    """
    if exts is None:
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

    buckets = defaultdict(list)
    total = 0

    for root, _, files in os.walk(image_dir):
        for file in files:
            lower = file.lower()
            if not any(lower.endswith(e) for e in exts):
                continue
            file_path = os.path.join(root, file)
            try:
                stat = os.path.getsize(file_path)
            except OSError:
                continue
            try:
                with Image.open(file_path) as img:
                    w, h = img.size
                    if h == 0:
                        continue
                    ratio = float(w) / float(h)
                    # normalized ratio bucket
                    if ratio_tol > 0:
                        round_ratio = round(ratio / ratio_tol) * ratio_tol
                    else:
                        round_ratio = round(ratio, 4)

                    size_bucket = int(stat / (size_bucket_mb * 1024 * 1024))

                    # compute hash
                    if method == "phash":
                        img_hash = imagehash.phash(img, hash_size=hash_size)
                    elif method == "dhash":
                        img_hash = imagehash.dhash(img, hash_size=hash_size)
                    else:
                        img_hash = imagehash.phash(img, hash_size=hash_size)

                    key_int = _hash_to_int(img_hash)
                    buckets[(round_ratio, size_bucket)].append((file_path, key_int))
                    total += 1
            except Exception as e:
                logger.debug(f"skipping {file_path}: {e}")

    logger.info(f"Collected {total} images into {len(buckets)} buckets")

    duplicates = []
    seen = set()

    for bkey, items in buckets.items():
        if not items:
            continue
        bk = BKTree()
        for path, key_int in items:
            # query existing tree for neighbors
            matches = bk.query(key_int, threshold)
            if matches:
                for mpath, dist in matches:
                    # avoid duplicated pair ordering
                    pair = (path, mpath)
                    if pair not in seen and (mpath, path) not in seen:
                        duplicates.append((path, mpath, dist))
                        seen.add(pair)
            else:
                bk.insert(key_int, path)

    logger.info(f"Found {len(duplicates)} duplicate pairs")
    return duplicates


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("dir")
    p.add_argument("--threshold", type=int, default=1)
    p.add_argument("--ratio-tol", type=float, default=0.02)
    p.add_argument("--size-mb", type=float, default=0.5)
    args = p.parse_args()
    dups = find_near_duplicates(args.dir, threshold=args.threshold, ratio_tol=args.ratio_tol, size_bucket_mb=args.size_mb)
    for a, b, dist in dups:
        print(f"{dist}\t{a}\t{b}")


def find_near_duplicates_from_list(
    file_list,
    threshold=1,
    ratio_tol=0.02,
    size_bucket_mb=0.5,
    method="phash",
    hash_size=8,
    exts=None,
):
    """
    Same as `find_near_duplicates` but accepts a pre-built list of file paths.
    """
    if exts is None:
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

    buckets = defaultdict(list)
    total = 0
    for file_path in file_list:
        lower = file_path.lower()
        if not any(lower.endswith(e) for e in exts):
            continue
        try:
            stat = os.path.getsize(file_path)
        except OSError:
            continue
        try:
            with Image.open(file_path) as img:
                w, h = img.size
                if h == 0:
                    continue
                ratio = float(w) / float(h)
                if ratio_tol > 0:
                    round_ratio = round(ratio / ratio_tol) * ratio_tol
                else:
                    round_ratio = round(ratio, 4)

                size_bucket = int(stat / (size_bucket_mb * 1024 * 1024))

                if method == "phash":
                    img_hash = imagehash.phash(img, hash_size=hash_size)
                elif method == "dhash":
                    img_hash = imagehash.dhash(img, hash_size=hash_size)
                else:
                    img_hash = imagehash.phash(img, hash_size=hash_size)

                key_int = _hash_to_int(img_hash)
                buckets[(round_ratio, size_bucket)].append((file_path, key_int))
                total += 1
        except Exception:
            continue

    duplicates = []
    seen = set()
    for bkey, items in buckets.items():
        if not items:
            continue
        bk = BKTree()
        for path, key_int in items:
            matches = bk.query(key_int, threshold)
            if matches:
                for mpath, dist in matches:
                    pair = (path, mpath)
                    if pair not in seen and (mpath, path) not in seen:
                        duplicates.append((path, mpath, dist))
                        seen.add(pair)
            else:
                bk.insert(key_int, path)

    return duplicates
