"""
Memory-safe FAISS pHash batch processor.

- Walks a dataset folder for images
- Extracts 64-bit pHash using multiprocessing in configurable batches
- Saves each batch as a shard (.npy for vectors and .txt for paths)
- Builds a FAISS IndexBinaryFlat incrementally by adding each shard
- Searches for near-duplicates with Hamming distance <= threshold
- Writes detected duplicate pairs to an output file

Usage:
    python faiss_batch_phash.py --image-dir X:\path\to\images --batch-size 50000 --processes 8

Requirements: numpy, faiss (faiss-cpu), pillow, imagehash
"""

from __future__ import annotations
import argparse
import os
import sys
import math
import time
from multiprocessing import Pool, cpu_count
from functools import partial
from pathlib import Path
from typing import List, Tuple, Optional

try:
    import numpy as np
except Exception as e:
    print("numpy is required: pip install numpy", file=sys.stderr)
    raise

try:
    import faiss
except Exception:
    print("faiss is required (faiss-cpu on Windows): pip install faiss-cpu", file=sys.stderr)
    raise

try:
    from PIL import Image
    import imagehash
except Exception as e:
    print("Pillow and imagehash are required: pip install pillow imagehash", file=sys.stderr)
    raise


def enumerate_image_paths(root: str, exts=None) -> List[str]:
    exts = exts or ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp')
    paths = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(exts):
                paths.append(os.path.join(dirpath, fn))
    return paths


def phash_to_bytes(phash: imagehash.ImageHash, hash_size: int = 8, bitorder: str = 'big') -> Optional[np.ndarray]:
    # imagehash.ImageHash.hash is a 2D numpy bool array (hash_size x hash_size)
    try:
        bits = phash.hash.flatten()
        packed = np.packbits(bits, bitorder=bitorder)
        # Ensure length is hash_size*hash_size/8
        bytes_needed = (hash_size * hash_size) // 8
        if packed.size != bytes_needed:
            packed = np.resize(packed, bytes_needed)
        return packed.astype(np.uint8)
    except Exception:
        return None


def worker_extract_phash(path: str, hash_size: int = 8, bitorder: str = 'big') -> Optional[Tuple[str, np.ndarray]]:
    try:
        with Image.open(path) as img:
            ph = imagehash.phash(img, hash_size=hash_size)
            b = phash_to_bytes(ph, hash_size=hash_size, bitorder=bitorder)
            if b is None:
                return None
            return path, b
    except Exception:
        return None


def chunked_iterable(it: List[str], size: int):
    for i in range(0, len(it), size):
        yield it[i:i + size]


def save_shard(shard_dir: str, shard_idx: int, vectors: np.ndarray, paths: List[str]):
    os.makedirs(shard_dir, exist_ok=True)
    vec_file = os.path.join(shard_dir, f"shard_{shard_idx:04d}.npy")
    paths_file = os.path.join(shard_dir, f"shard_{shard_idx:04d}_paths.txt")
    np.save(vec_file, vectors)
    with open(paths_file, 'w', encoding='utf-8') as fh:
        for p in paths:
            fh.write(p.replace('\\', '/') + '\n')
    return vec_file, paths_file


def build_and_search(image_dir: str,
                     batch_size: int = 50000,
                     processes: int = None,
                     hash_size: int = 8,
                     threshold: int = 1,
                     shard_dir: str = "shards",
                     out_pairs_file: str = "duplicate_pairs.txt",
                     verbose: bool = True):

    processes = int(processes or max(1, cpu_count() - 1))
    paths = enumerate_image_paths(image_dir)
    n = len(paths)
    if n == 0:
        print("No images found")
        return
    if verbose:
        print(f"Found {n:,} images; processing in batches of {batch_size:,} with {processes} workers")

    bytes_per_vec = (hash_size * hash_size) // 8
    d = hash_size * hash_size  # bits
    index = faiss.IndexBinaryFlat(d)

    global_paths: List[str] = []
    shard_idx = 0
    start = time.time()

    with Pool(processes) as pool:
        for batch_paths in chunked_iterable(paths, batch_size):
            if verbose:
                print(f"Processing shard {shard_idx} with {len(batch_paths):,} images...")
            func = partial(worker_extract_phash, hash_size=hash_size)
            results = pool.map(func, batch_paths)
            valid = [r for r in results if r is not None]
            if not valid:
                shard_idx += 1
                continue

            batch_paths_clean, batch_vecs = zip(*valid)
            batch_vecs_arr = np.vstack(batch_vecs).astype(np.uint8)

            # Save shard to disk for persistence
            save_shard(shard_dir, shard_idx, batch_vecs_arr, list(batch_paths_clean))

            # Add to FAISS index incrementally (does not require merging into a single big array)
            index.add(batch_vecs_arr)

            global_paths.extend(batch_paths_clean)
            if verbose:
                print(f"Shard {shard_idx} added. Index size now {index.ntotal:,}")
            shard_idx += 1

    elapsed = time.time() - start
    if verbose:
        print(f"Index build complete: {index.ntotal:,} vectors in {elapsed:.1f}s")

    # Now search for nearest neighbors (K=2 to include self + nearest other)
    K = 2
    duplicate_pairs = set()

    # Search per-shard again to keep memory low: load each shard file and query the index
    shard_files = sorted([f for f in os.listdir(shard_dir) if f.endswith('.npy')])
    total_queries = 0
    for sf in shard_files:
        vecs = np.load(os.path.join(shard_dir, sf))
        if vecs.shape[0] == 0:
            continue
        distances, indices = index.search(vecs, K)
        # compute base index for this shard
        # We need to know the starting global index of the vectors in this shard.
        # Since we added shards sequentially and appended to global_paths, we can compute offsets.
        # Find the first path of this shard in global_paths
        idx_in_name = int(sf.split('_')[1].split('.')[0])
        # compute offset by summing sizes of earlier shards files
        offset = 0
        for earlier in shard_files:
            ei = int(earlier.split('_')[1].split('.')[0])
            if ei >= idx_in_name:
                break
            arr = np.load(os.path.join(shard_dir, earlier))
            offset += arr.shape[0]
        # now iterate
        for i in range(vecs.shape[0]):
            q_global = offset + i
            total_queries += 1
            # examine neighbors (skip index 0 which is likely self)
            for k in range(1, K):
                neigh = int(indices[i][k])
                if neigh == -1:
                    continue
                dist = int(distances[i][k])
                if dist <= threshold:
                    a, b = min(q_global, neigh), max(q_global, neigh)
                    pair = (global_paths[a], global_paths[b])
                    duplicate_pairs.add(pair)

    if verbose:
        print(f"Searched {total_queries:,} queries; found {len(duplicate_pairs):,} candidate pairs")

    # Write result file
    with open(out_pairs_file, 'w', encoding='utf-8') as fh:
        for a, b in sorted(duplicate_pairs):
            fh.write(f"{a}\t{b}\n")

    if verbose:
        print(f"Duplicate pairs written to {out_pairs_file}")
    return duplicate_pairs


def build_and_search_return_pairs(image_dir: str,
                                  batch_size: int = 50000,
                                  processes: int = None,
                                  hash_size: int = 8,
                                  threshold: int = 1,
                                  shard_dir: str = "shards",
                                  verbose: bool = True):
    """Compatibility wrapper that returns duplicate pairs set instead of only writing files."""
    return build_and_search(
        image_dir=image_dir,
        batch_size=batch_size,
        processes=processes,
        hash_size=hash_size,
        threshold=threshold,
        shard_dir=shard_dir,
        out_pairs_file=f"duplicate_pairs_{int(time.time())}.txt",
        verbose=verbose
    )


def build_and_search_from_paths(paths: List[str],
                                batch_size: int = 50000,
                                processes: int = None,
                                hash_size: int = 8,
                                threshold: int = 1,
                                shard_dir: str = "shards",
                                verbose: bool = True) -> set:
    """Process a given list of image file paths (no directory walk) and return duplicate pairs."""
    processes = int(processes or max(1, cpu_count() - 1))
    n = len(paths)
    if n == 0:
        if verbose:
            print("No images provided")
        return set()
    if verbose:
        print(f"Processing {n:,} provided images in batches of {batch_size:,} with {processes} workers")

    bytes_per_vec = (hash_size * hash_size) // 8
    d = hash_size * hash_size  # bits
    index = faiss.IndexBinaryFlat(d)

    global_paths: List[str] = []
    shard_idx = 0
    start = time.time()

    with Pool(processes) as pool:
        for batch_paths in chunked_iterable(paths, batch_size):
            if verbose:
                print(f"Processing shard {shard_idx} with {len(batch_paths):,} images...")
            func = partial(worker_extract_phash, hash_size=hash_size)
            results = pool.map(func, batch_paths)
            valid = [r for r in results if r is not None]
            if not valid:
                shard_idx += 1
                continue

            batch_paths_clean, batch_vecs = zip(*valid)
            batch_vecs_arr = np.vstack(batch_vecs).astype(np.uint8)

            # Save shard to disk for persistence
            save_shard(shard_dir, shard_idx, batch_vecs_arr, list(batch_paths_clean))

            # Add to FAISS index incrementally
            index.add(batch_vecs_arr)

            global_paths.extend(batch_paths_clean)
            if verbose:
                print(f"Shard {shard_idx} added. Index size now {index.ntotal:,}")
            shard_idx += 1

    elapsed = time.time() - start
    if verbose:
        print(f"Index build complete: {index.ntotal:,} vectors in {elapsed:.1f}s")

    # Search
    K = 2
    duplicate_pairs = set()
    shard_files = sorted([f for f in os.listdir(shard_dir) if f.endswith('.npy')])
    total_queries = 0
    for sf in shard_files:
        vecs = np.load(os.path.join(shard_dir, sf))
        if vecs.shape[0] == 0:
            continue
        distances, indices = index.search(vecs, K)
        idx_in_name = int(sf.split('_')[1].split('.')[0])
        offset = 0
        for earlier in shard_files:
            ei = int(earlier.split('_')[1].split('.')[0])
            if ei >= idx_in_name:
                break
            arr = np.load(os.path.join(shard_dir, earlier))
            offset += arr.shape[0]
        for i in range(vecs.shape[0]):
            q_global = offset + i
            total_queries += 1
            for k in range(1, K):
                neigh = int(indices[i][k])
                if neigh == -1:
                    continue
                dist = int(distances[i][k])
                if dist <= threshold:
                    a, b = min(q_global, neigh), max(q_global, neigh)
                    pair = (global_paths[a], global_paths[b])
                    duplicate_pairs.add(pair)

    if verbose:
        print(f"Searched {total_queries:,} queries; found {len(duplicate_pairs):,} candidate pairs")

    return duplicate_pairs


def parse_args():
    p = argparse.ArgumentParser(description="FAISS batch pHash duplicate finder")
    p.add_argument("--image-dir", required=True)
    p.add_argument("--batch-size", type=int, default=50000)
    p.add_argument("--processes", type=int, default=None)
    p.add_argument("--hash-size", type=int, default=8, help="hash_size for imagehash.phash (8 => 64-bit)")
    p.add_argument("--threshold", type=int, default=1, help="Hamming distance threshold for duplicate")
    p.add_argument("--shard-dir", default="shards")
    p.add_argument("--out", default="duplicate_pairs.txt")
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()
    build_and_search(
        image_dir=args.image_dir,
        batch_size=args.batch_size,
        processes=args.processes,
        hash_size=args.hash_size,
        threshold=args.threshold,
        shard_dir=args.shard_dir,
        out_pairs_file=args.out,
        verbose=True
    )
