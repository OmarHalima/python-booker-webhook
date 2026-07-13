"""Shared helpers for BMEIA captcha OCR training dataset workflows."""
from __future__ import annotations

import csv
import hashlib
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

MANIFEST_FIELDS = [
    "id",
    "filename",
    "path",
    "sha256",
    "vcid",
    "vcid_tail",
    "difficulty",
    "ocr_guess",
    "label",
    "verified",
    "ocr_was_wrong",
    "split",
    "created_at",
]

LABEL_RE = re.compile(r"^[A-Z0-9]{4,8}$")


def default_dataset_dir(root: Path) -> Path:
    return root / "datasets" / "bmeia-captcha-v1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_label(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def validate_label(text: str) -> bool:
    return bool(LABEL_RE.match(normalize_label(text)))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def manifest_path(dataset_dir: Path) -> Path:
    return dataset_dir / "manifest.csv"


def raw_dir(dataset_dir: Path) -> Path:
    return dataset_dir / "raw"


def splits_dir(dataset_dir: Path) -> Path:
    return dataset_dir / "splits"


def empty_row(**overrides: Any) -> Dict[str, str]:
    row = {field: "" for field in MANIFEST_FIELDS}
    row["verified"] = "false"
    row["ocr_was_wrong"] = ""
    for key, value in overrides.items():
        if key in MANIFEST_FIELDS:
            row[key] = str(value) if value is not None else ""
    return row


def load_manifest(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows: List[Dict[str, str]] = []
        for raw in reader:
            row = empty_row()
            for field in MANIFEST_FIELDS:
                if field in raw and raw[field] is not None:
                    row[field] = str(raw[field])
            rows.append(row)
        return rows


def save_manifest(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in MANIFEST_FIELDS})


def known_sha256(rows: Iterable[Dict[str, str]]) -> Set[str]:
    return {row["sha256"] for row in rows if row.get("sha256")}


def next_row_id(rows: Sequence[Dict[str, str]]) -> int:
    max_id = 0
    for row in rows:
        try:
            max_id = max(max_id, int(row.get("id") or "0"))
        except ValueError:
            continue
    return max_id + 1


def assess_difficulty(img_bytes: bytes) -> str:
    import booker_legacy as booker

    return booker._assess_captcha_difficulty(img_bytes)


def upsert_row(rows: List[Dict[str, str]], row: Dict[str, str]) -> List[Dict[str, str]]:
    sha = row.get("sha256", "")
    for idx, existing in enumerate(rows):
        if existing.get("sha256") == sha and sha:
            merged = dict(existing)
            merged.update(row)
            rows[idx] = merged
            return rows
    rows.append(row)
    return rows


def split_verified_rows(
    rows: Sequence[Dict[str, str]],
    *,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    seed: int = 42,
) -> Dict[str, List[Dict[str, str]]]:
    verified = [row for row in rows if (row.get("verified") or "").lower() == "true" and row.get("label")]
    if not verified:
        return {"train": [], "val": [], "test": []}

    by_difficulty: Dict[str, List[Dict[str, str]]] = {"easy": [], "medium": [], "hard": [], "unknown": []}
    for row in verified:
        bucket = (row.get("difficulty") or "unknown").lower()
        if bucket not in by_difficulty:
            bucket = "unknown"
        by_difficulty[bucket].append(row)

    rng = random.Random(seed)
    train: List[Dict[str, str]] = []
    val: List[Dict[str, str]] = []
    test: List[Dict[str, str]] = []

    for bucket_rows in by_difficulty.values():
        items = list(bucket_rows)
        rng.shuffle(items)
        n = len(items)
        n_train = int(n * train_ratio)
        n_val = int(n * val_ratio)
        n_test = max(0, n - n_train - n_val)
        if n_train + n_val + n_test > n:
            n_test = max(0, n - n_train - n_val)
        train.extend(items[:n_train])
        val.extend(items[n_train : n_train + n_val])
        test.extend(items[n_train + n_val : n_train + n_val + n_test])

    for subset, name in ((train, "train"), (val, "val"), (test, "test")):
        for row in subset:
            row["split"] = name

    return {"train": train, "val": val, "test": test}


def write_split_csvs(dataset_dir: Path, rows: Sequence[Dict[str, str]]) -> Dict[str, Path]:
    out = splits_dir(dataset_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for split_name in ("train", "val", "test"):
        split_rows = [row for row in rows if row.get("split") == split_name]
        path = out / f"{split_name}.csv"
        save_manifest(path, split_rows)
        paths[split_name] = path
    return paths
