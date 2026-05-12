#!/usr/bin/env python3
"""
Chunk inspection utility — connects to ChromaDB and reports on chunk quality.

Usage:
  python inspect_chunks.py
  python inspect_chunks.py --sample 20 --short-threshold 80
"""

import argparse
import os
import random
import sys

import chromadb
import defaults

CHROMA_HOST = os.getenv("CHROMA_HOST", defaults.CHROMA_HOST)
CHROMA_PORT = int(os.getenv("CHROMA_PORT", defaults.CHROMA_PORT))
COLLECTION  = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)

# 1 token ≈ 4 chars; chunks at ≥95% of this are likely hitting the cap
_TOKEN_CAP       = int(os.getenv("CHUNK_SIZE", defaults.CHUNK_SIZE))
_CAP_CHARS       = _TOKEN_CAP * 4
_CAP_THRESHOLD   = int(_CAP_CHARS * 0.95)


# ── ChromaDB helpers ──────────────────────────────────────────────────────────

def _fetch_all(collection: chromadb.Collection, batch_size: int = 500):
    total = collection.count()
    docs, metas, ids = [], [], []
    offset = 0
    while offset < total:
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        docs.extend(result["documents"])
        metas.extend(result["metadatas"])
        ids.extend(result["ids"])
        offset += batch_size
    return docs, metas, ids


# ── Display helpers ───────────────────────────────────────────────────────────

def _percentile(sorted_vals: list, p: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(int(len(sorted_vals) * p / 100), len(sorted_vals) - 1)
    return sorted_vals[idx]


def _histogram(values: list, bins: int = 8, bar_width: int = 28) -> list:
    """Return list of (range_label, count, bar_str) for a text histogram."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [(f"{lo:,}", len(values), "█" * bar_width)]
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / step), bins - 1)
        counts[idx] += 1
    max_count = max(counts) or 1
    rows = []
    for i, count in enumerate(counts):
        lo_b = int(lo + i * step)
        hi_b = int(lo + (i + 1) * step)
        bar  = "█" * int(count / max_count * bar_width)
        rows.append((f"{lo_b:>5,}–{hi_b:<5,}", count, bar))
    return rows


def _truncate(text: str, max_chars: int = 400) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n  …[+{len(text) - max_chars:,} chars]"


# ── Main ──────────────────────────────────────────────────────────────────────

def main(sample_n: int = 10, short_threshold: int = 100) -> None:
    SEP  = "=" * 62
    THIN = "-" * 62

    print(f"\nConnecting to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT} …")
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

    try:
        collection = client.get_collection(COLLECTION)
    except Exception as e:
        print(f"ERROR: cannot open collection {COLLECTION!r}: {e}")
        sys.exit(1)

    total = collection.count()
    if total == 0:
        print("Collection is empty — nothing to inspect.")
        sys.exit(0)

    print(f"Fetching {total:,} chunks …")
    docs, metas, ids = _fetch_all(collection)

    lengths        = [len(d) for d in docs]
    sorted_lengths = sorted(lengths)

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  CHUNK INSPECTION REPORT")
    print(f"  Collection : {COLLECTION!r}")
    print(f"  Chunks     : {total:,}")
    print(SEP)

    # ── Length distribution ───────────────────────────────────────────────────
    print("\nLENGTH DISTRIBUTION  (characters; ~4 chars ≈ 1 token)")
    stats = [
        ("min", sorted_lengths[0]),
        ("p10", _percentile(sorted_lengths, 10)),
        ("p25", _percentile(sorted_lengths, 25)),
        ("p50", _percentile(sorted_lengths, 50)),
        ("p75", _percentile(sorted_lengths, 75)),
        ("p90", _percentile(sorted_lengths, 90)),
        ("max", sorted_lengths[-1]),
    ]
    for label, val in stats:
        print(f"  {label:>3} : {val:>6,}  (~{val // 4:,} tokens)")
    print()
    for label, count, bar in _histogram(lengths):
        pct = count / total * 100
        print(f"  [{label}]  {bar:<28}  {count:>5,}  ({pct:.1f}%)")

    # ── Metadata completeness ─────────────────────────────────────────────────
    print("\nMETADATA COMPLETENESS")
    has_heading = sum(1 for m in metas if m.get("headings"))
    has_page    = sum(1 for m in metas if m.get("page") is not None)
    has_folder  = sum(1 for m in metas if m.get("folder"))
    for label, n in [("headings", has_heading), ("page number", has_page), ("folder", has_folder)]:
        print(f"  {label:<14} : {n:>6,} / {total:,}  ({n / total * 100:.1f}%)")

    # ── Red flags ─────────────────────────────────────────────────────────────
    print("\nRED FLAGS")
    short_items = [(i, docs[i], metas[i]) for i, l in enumerate(lengths) if l < short_threshold]
    cap_items   = [(i, docs[i], metas[i]) for i, l in enumerate(lengths) if l >= _CAP_THRESHOLD]

    print(f"  chunks < {short_threshold} chars   (possible junk)       : {len(short_items):>5,}  ({len(short_items) / total * 100:.1f}%)")
    print(f"  chunks ≥ ~{_TOKEN_CAP} tokens (~{_CAP_CHARS:,} chars) (may be cut off): {len(cap_items):>5,}  ({len(cap_items) / total * 100:.1f}%)")

    if short_items:
        print(f"\n  Short chunk samples (up to 5):")
        for _, text, meta in short_items[:5]:
            fname = meta.get("filename", "?")
            page  = meta.get("page", "?")
            print(f"    [{fname}  p{page}]  {text!r}")

    # ── Per-file summary ──────────────────────────────────────────────────────
    print("\nPER-FILE SUMMARY")
    file_counts: dict[str, dict] = {}
    for i, meta in enumerate(metas):
        fname = meta.get("filename", "unknown")
        entry = file_counts.setdefault(fname, {"chunks": 0, "chars": []})
        entry["chunks"] += 1
        entry["chars"].append(lengths[i])
    print(f"  {'File':<40}  {'Chunks':>6}  {'Avg chars':>9}  {'Min':>6}  {'Max':>6}")
    print(f"  {THIN}")
    for fname, info in sorted(file_counts.items()):
        avg = sum(info["chars"]) // len(info["chars"])
        print(f"  {fname:<40}  {info['chunks']:>6,}  {avg:>9,}  {min(info['chars']):>6,}  {max(info['chars']):>6,}")

    # ── Visual sample ─────────────────────────────────────────────────────────
    n = min(sample_n, total)
    print(f"\n{SEP}")
    print(f"  RANDOM SAMPLE  ({n} of {total:,} chunks)")
    print(SEP)

    indices = random.sample(range(len(docs)), n)
    for rank, idx in enumerate(indices, 1):
        text = docs[idx]
        meta = metas[idx]
        fname    = meta.get("filename", "?")
        page     = meta.get("page", "?")
        headings = meta.get("headings", "")
        print(f"\n[{rank}/{n}]  {fname}  |  page {page}  |  {len(text):,} chars  (~{len(text) // 4} tokens)")
        if headings:
            print(f"  headings : {headings}")
        print(f"  {THIN}")
        for line in _truncate(text, 400).splitlines():
            print(f"  {line}")

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect ChromaDB chunk quality")
    parser.add_argument("--sample",          type=int, default=10,  help="Random chunks to display (default: 10)")
    parser.add_argument("--short-threshold", type=int, default=100, help="Flag chunks shorter than N chars (default: 100)")
    args = parser.parse_args()
    main(sample_n=args.sample, short_threshold=args.short_threshold)
