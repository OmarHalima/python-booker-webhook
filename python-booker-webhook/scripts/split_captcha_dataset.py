#!/usr/bin/env python3
"""Create stratified train/val/test splits from verified manifest rows.

Usage:
  python scripts/split_captcha_dataset.py
  python scripts/split_captcha_dataset.py --train 0.8 --val 0.1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import (
    default_dataset_dir,
    load_manifest,
    manifest_path,
    save_manifest,
    split_verified_rows,
    write_split_csvs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Split verified captcha dataset.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--train", type=float, default=0.8)
    parser.add_argument("--val", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    manifest = manifest_path(dataset_dir)
    rows = load_manifest(manifest)
    if not rows:
        print(f"ERROR: empty manifest at {manifest}", file=sys.stderr)
        return 1

    verified_count = sum(1 for r in rows if (r.get("verified") or "").lower() == "true")
    if verified_count == 0:
        print("ERROR: no verified rows — run review_captcha_labels.py first", file=sys.stderr)
        return 1

    subsets = split_verified_rows(rows, train_ratio=args.train, val_ratio=args.val, seed=args.seed)
    save_manifest(manifest, rows)
    paths = write_split_csvs(dataset_dir, rows)

    summary = {
        "ok": True,
        "verified": verified_count,
        "train": len(subsets["train"]),
        "val": len(subsets["val"]),
        "test": len(subsets["test"]),
        "splits": {name: str(path) for name, path in paths.items()},
    }
    (dataset_dir / "split_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
