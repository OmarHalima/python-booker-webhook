"""Unit tests for captcha training dataset helpers."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import (
    empty_row,
    load_manifest,
    normalize_label,
    save_manifest,
    split_verified_rows,
    validate_label,
)


def test_normalize_and_validate_label():
    assert normalize_label(" ab-12x ") == "AB12X"
    assert validate_label("Y5X4Y")
    assert not validate_label("AB")
    assert not validate_label("bad!")


def test_manifest_roundtrip(tmp_path):
    manifest = tmp_path / "manifest.csv"
    rows = [
        empty_row(id=1, filename="a.png", path="raw/a.png", sha256="abc", difficulty="easy"),
        empty_row(id=2, filename="b.png", path="raw/b.png", sha256="def", difficulty="hard"),
    ]
    save_manifest(manifest, rows)
    loaded = load_manifest(manifest)
    assert len(loaded) == 2
    assert loaded[0]["filename"] == "a.png"
    assert loaded[1]["difficulty"] == "hard"
    assert loaded[0]["verified"] == "false"


def test_split_verified_rows_stratified():
    rows = [
        empty_row(id=i, sha256=f"sha{i}", label="ABCDE", verified="true", difficulty=diff)
        for i, diff in enumerate(["easy"] * 10 + ["hard"] * 10)
    ]
    subsets = split_verified_rows(rows, train_ratio=0.8, val_ratio=0.1, seed=1)
    assert len(subsets["train"]) == 16
    assert len(subsets["val"]) == 2
    assert len(subsets["test"]) == 2
    assert all(r.get("split") == "train" for r in subsets["train"])
