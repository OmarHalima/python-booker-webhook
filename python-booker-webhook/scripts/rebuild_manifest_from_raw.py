#!/usr/bin/env python3
"""Index orphaned raw/*.png files into manifest.csv (resume-safe by sha256).

Filename pattern from harvest: {id:06d}_{vcid_tail8}_{sha256_prefix8}.png
Full VCID is not recoverable from the filename; vcid_tail is stored.

Usage:
  python scripts/rebuild_manifest_from_raw.py
  python scripts/rebuild_manifest_from_raw.py --dataset-dir datasets/bmeia-captcha-v1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import (
    assess_difficulty,
    default_dataset_dir,
    empty_row,
    known_sha256,
    load_manifest,
    manifest_path,
    next_row_id,
    raw_dir,
    save_manifest,
    sha256_bytes,
    utc_now_iso,
)

FILENAME_RE = re.compile(r"^(\d{6})_([a-f0-9]{8})_([a-f0-9]{8})\.png$", re.IGNORECASE)


def rebuild(*, dataset_dir: Path, skip_difficulty: bool) -> dict:
    manifest = manifest_path(dataset_dir)
    rows = load_manifest(manifest)
    seen = known_sha256(rows)
    row_id = next_row_id(rows)

    added = 0
    skipped = 0
    prefix_mismatch = 0
    t0 = time.perf_counter()

    for png in sorted(raw_dir(dataset_dir).glob("*.png")):
        data = png.read_bytes()
        digest = sha256_bytes(data)
        if digest in seen:
            skipped += 1
            continue

        match = FILENAME_RE.match(png.name)
        vcid_tail = match.group(2) if match else ""
        prefix = match.group(3) if match else ""
        if prefix and not digest.startswith(prefix.lower()):
            prefix_mismatch += 1

        difficulty = "unknown"
        if not skip_difficulty:
            try:
                difficulty = assess_difficulty(data)
            except Exception:
                difficulty = "unknown"

        row = empty_row(
            id=row_id,
            filename=png.name,
            path=f"raw/{png.name}",
            sha256=digest,
            vcid="",
            vcid_tail=vcid_tail,
            difficulty=difficulty,
            created_at=utc_now_iso(),
        )
        rows.append(row)
        seen.add(digest)
        row_id += 1
        added += 1
        if added % 500 == 0:
            print(f"indexed {added} new rows...", flush=True)

    save_manifest(manifest, rows)
    summary = {
        "ok": True,
        "datasetDir": str(dataset_dir),
        "manifest": str(manifest),
        "totalRows": len(rows),
        "added": added,
        "skippedExistingSha256": skipped,
        "prefixMismatch": prefix_mismatch,
        "rebuildMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    (dataset_dir / "rebuild_manifest_report.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild manifest.csv from raw PNG files.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument(
        "--skip-difficulty",
        action="store_true",
        help="Skip booker difficulty assessment (faster; sets difficulty=unknown)",
    )
    args = parser.parse_args()

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    if not raw_dir(dataset_dir).is_dir():
        print(f"ERROR: raw dir missing: {raw_dir(dataset_dir)}", file=sys.stderr)
        return 1

    summary = rebuild(dataset_dir=dataset_dir, skip_difficulty=args.skip_difficulty)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
