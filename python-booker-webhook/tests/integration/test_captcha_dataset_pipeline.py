"""Integration smoke tests for captcha dataset pipeline scripts."""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import empty_row, load_manifest, manifest_path, raw_dir, save_manifest
from review_captcha_labels import ReviewState, make_handler
from http.server import ThreadingHTTPServer

# Minimal valid 1x1 white PNG
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    out_raw = raw_dir(dataset)
    out_raw.mkdir(parents=True)
    filename = "000001_testvcid_aabbccdd.png"
    (out_raw / filename).write_bytes(TINY_PNG)
    rows = [
        empty_row(
            id=1,
            filename=filename,
            path=f"raw/{filename}",
            sha256="a" * 64,
            vcid="testvcid12345678",
            vcid_tail="12345678",
            difficulty="easy",
            ocr_guess="AB12C",
        ),
        empty_row(
            id=2,
            filename="000002_testvcid_bbbbccdd.png",
            path="raw/000002_testvcid_bbbbccdd.png",
            sha256="b" * 64,
            vcid="testvcid87654321",
            vcid_tail="87654321",
            difficulty="hard",
            ocr_guess="XY9Z1",
        ),
    ]
    (out_raw / rows[1]["filename"]).write_bytes(TINY_PNG)
    save_manifest(manifest_path(dataset), rows)
    return dataset


def test_review_ui_accepts_and_verifies(sample_dataset):
    state = ReviewState(sample_dataset)
    result = state.save_action("1", "accept", "AB12C")
    assert result["ok"] is True

    rows = load_manifest(manifest_path(sample_dataset))
    row1 = next(r for r in rows if r["id"] == "1")
    assert row1["verified"] == "true"
    assert row1["label"] == "AB12C"
    assert row1["ocr_was_wrong"] == "false"


def test_review_accept_ignores_edited_label(sample_dataset):
    state = ReviewState(sample_dataset)
    result = state.save_action("1", "accept", "ZZZZZ")
    assert result["ok"] is True

    rows = load_manifest(manifest_path(sample_dataset))
    row1 = next(r for r in rows if r["id"] == "1")
    assert row1["label"] == "AB12C"
    assert row1["ocr_was_wrong"] == "false"


def test_review_accept_fails_without_ocr_guess(sample_dataset):
    rows = load_manifest(manifest_path(sample_dataset))
    rows[0]["ocr_guess"] = ""
    save_manifest(manifest_path(sample_dataset), rows)

    state = ReviewState(sample_dataset)
    result = state.save_action("1", "accept", "")
    assert result["ok"] is False
    assert "OCR" in result["error"]


def test_review_save_preserves_externally_added_rows(sample_dataset):
    state = ReviewState(sample_dataset)
    rows = load_manifest(manifest_path(sample_dataset))
    rows.append(
        empty_row(
            id=3,
            filename="000003_extra.png",
            path="raw/000003_extra.png",
            sha256="c" * 64,
            ocr_guess="EXTRA1",
        )
    )
    save_manifest(manifest_path(sample_dataset), rows)

    result = state.save_action("1", "accept", "AB12C")
    assert result["ok"] is True

    final = load_manifest(manifest_path(sample_dataset))
    assert len(final) == 3
    assert any(r["id"] == "3" for r in final)


def test_review_manual_label_without_ocr_marks_wrong(sample_dataset):
    rows = load_manifest(manifest_path(sample_dataset))
    rows[0]["ocr_guess"] = ""
    save_manifest(manifest_path(sample_dataset), rows)

    state = ReviewState(sample_dataset)
    result = state.save_action("1", "save", "MANUAL")
    assert result["ok"] is True

    rows = load_manifest(manifest_path(sample_dataset))
    row1 = next(r for r in rows if r["id"] == "1")
    assert row1["label"] == "MANUAL"
    assert row1["ocr_was_wrong"] == "true"


def test_review_stats_pending_respects_filter(sample_dataset):
    state = ReviewState(sample_dataset, difficulty_filter="hard", show_all=True)
    stats_before = state.stats()
    assert stats_before["pending"] == 1
    assert stats_before["verified"] == 0

    state.save_action("2", "accept", "XY9Z1")

    stats_after = state.stats()
    assert stats_after["verified"] == 1
    assert stats_after["pending"] == 0


def test_review_ui_marks_ocr_correction(sample_dataset):
    state = ReviewState(sample_dataset)
    result = state.save_action("1", "save", "AB99C")
    assert result["ok"] is True

    rows = load_manifest(manifest_path(sample_dataset))
    row1 = next(r for r in rows if r["id"] == "1")
    assert row1["label"] == "AB99C"
    assert row1["ocr_was_wrong"] == "true"


def test_review_list_and_unverify(sample_dataset):
    state = ReviewState(sample_dataset, show_all=True)
    state.save_action("1", "accept", "AB12C")
    assert len(state.list_items()) == 2

    state.save_action("1", "unverify", "")
    rows = load_manifest(manifest_path(sample_dataset))
    row1 = next(r for r in rows if r["id"] == "1")
    assert row1["verified"] == "false"
    assert row1["label"] == ""


def test_review_http_server_serves_stats(sample_dataset):
    state = ReviewState(sample_dataset)
    handler = make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["total"] == 2
        assert data["pending"] == 2
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/list", timeout=5) as resp:
            listed = json.loads(resp.read().decode("utf-8"))
        assert len(listed["items"]) == 2
    finally:
        server.shutdown()


def test_split_writes_csv_files(sample_dataset):
    state = ReviewState(sample_dataset)
    state.save_action("1", "accept", "AB12C")
    state.save_action("2", "accept", "XY9Z1")

    from captcha_dataset_lib import load_manifest, manifest_path, save_manifest, split_verified_rows, write_split_csvs

    rows = load_manifest(manifest_path(sample_dataset))
    subsets = split_verified_rows(rows, train_ratio=0.5, val_ratio=0.25, seed=42)
    save_manifest(manifest_path(sample_dataset), rows)
    paths = write_split_csvs(sample_dataset, rows)

    assert len(subsets["train"]) + len(subsets["val"]) + len(subsets["test"]) == 2
    assert paths["train"].is_file()
    assert paths["val"].is_file()
    assert paths["test"].is_file()


def test_prelabel_skips_without_ocr_url(sample_dataset, monkeypatch):
    prelabel_mod = _load_module("ocr_prelabel_manifest", SCRIPTS / "ocr_prelabel_manifest.py")
    monkeypatch.setattr(prelabel_mod, "captcha_ocr_base_url", lambda: "")
    with pytest.raises(ValueError, match="CAPTCHA_OCR_BASE_URL"):
        prelabel_mod.prelabel(dataset_dir=sample_dataset, force=False, enhance=False, timeout=10)


def test_prelabel_updates_manifest(sample_dataset, monkeypatch):
    monkeypatch.setenv("CAPTCHA_OCR_BASE_URL", "http://ocr.test")

    def fake_predict(base, img_bytes, filename="captcha.png", timeout=None):
        return {"result": "AB12C"}

    prelabel_mod = _load_module("ocr_prelabel_manifest", SCRIPTS / "ocr_prelabel_manifest.py")
    monkeypatch.setattr(prelabel_mod, "predict_file", fake_predict)

    summary = prelabel_mod.prelabel(dataset_dir=sample_dataset, force=True, enhance=False, timeout=10)
    assert summary["labeled"] == 2
    rows = load_manifest(manifest_path(sample_dataset))
    assert all(r["ocr_guess"] == "AB12C" for r in rows)
