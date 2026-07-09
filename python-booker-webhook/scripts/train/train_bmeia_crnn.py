#!/usr/bin/env python3
"""Finetune Nischay103 captcha_model.pt on verified BMEIA manifest splits.

Uses the same JIT CRNN + Tokenizer stack as local OCR (direct_server.py).
Run with the captcha_recognition venv (has torch):

  C:\\Users\\WD\\Desktop\\captcha_recognition\\env\\Scripts\\python.exe ^
    scripts/train/train_bmeia_crnn.py ^
    --dataset-dir datasets/bmeia-captcha-v1 ^
    --model-path C:/Users/WD/Desktop/captcha_recognition/models/captcha_model.pt ^
    --ocr-repo C:/Users/WD/Desktop/captcha_recognition

Prerequisites:
  python scripts/split_captcha_dataset.py
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import default_dataset_dir, load_manifest, normalize_label  # noqa: E402

CHARSET = r"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _import_torch_stack(ocr_repo: Path):
    repo = ocr_repo.resolve()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from PIL import Image
    from torch.utils.data import DataLoader, Dataset
    from torchvision import transforms as T
    from tokenizer import Tokenizer

    return torch, nn, F, Image, DataLoader, Dataset, T, Tokenizer


def _encode_targets(tokenizer, labels: Sequence[str], seq_len: int, device) -> "torch.Tensor":
    import torch

    rows = []
    for label in labels:
        enc = tokenizer.encode([normalize_label(label)], device=device)[0]
        if enc.size(0) < seq_len:
            enc = torch.nn.functional.pad(
                enc, (0, seq_len - enc.size(0)), value=tokenizer.pad_id
            )
        else:
            enc = enc[:seq_len]
        rows.append(enc)
    return torch.stack(rows, dim=0)


def _train_loss(F, tokenizer, logits, labels, device) -> "torch.Tensor":
    seq_len = logits.size(1)
    targets = _encode_targets(tokenizer, labels, seq_len, device)
    return F.cross_entropy(
        logits.reshape(-1, logits.size(-1)),
        targets.reshape(-1),
        ignore_index=tokenizer.pad_id,
    )


def _load_split_rows(dataset_dir: Path, split: str) -> List[Dict[str, str]]:
    path = dataset_dir / "splits" / f"{split}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing split file: {path} — run split_captcha_dataset.py first")
    return load_manifest(path)


def _accuracy(
    torch,
    model,
    tokenizer,
    transform,
    rows: Sequence[Dict[str, str]],
    dataset_dir: Path,
    device,
) -> Tuple[int, int]:
    correct = 0
    total = 0
    model.eval()
    with torch.no_grad():
        for row in rows:
            label = normalize_label(row.get("label", ""))
            rel = (row.get("path") or f"raw/{row.get('filename', '')}").replace("\\", "/")
            img_path = (dataset_dir / rel).resolve()
            if not label or not img_path.is_file():
                continue
            from PIL import Image

            tensor = transform(Image.open(img_path).convert("RGB")).unsqueeze(0).to(device)
            probs = model(tensor)
            preds, _ = tokenizer.decode(probs, beam_width=1)
            pred = normalize_label(preds[0])
            total += 1
            if pred == label:
                correct += 1
    return correct, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Finetune BMEIA captcha CRNN on verified splits.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path(r"C:\Users\WD\Desktop\captcha_recognition\models\captcha_model.pt"),
    )
    parser.add_argument(
        "--ocr-repo",
        type=Path,
        default=Path(r"C:\Users\WD\Desktop\captcha_recognition"),
        help="Path to captcha_recognition repo (tokenizer.py)",
    )
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=1, help="Must be 1 for PARSeq JIT model")
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    torch, nn, F, Image, DataLoader, Dataset, T, Tokenizer = _import_torch_stack(args.ocr_repo)

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    train_rows = _load_split_rows(dataset_dir, "train")
    val_rows = _load_split_rows(dataset_dir, "val")
    test_rows = _load_split_rows(dataset_dir, "test")
    if not train_rows:
        print("ERROR: train split is empty — verify labels and run split_captcha_dataset.py", file=sys.stderr)
        return 1

    out_dir = (args.out_dir or (dataset_dir / "runs" / time.strftime("%Y%m%d-%H%M%S"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = torch.device(args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu")
    print(f"Device: {device}" + (f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else ""), flush=True)
    tokenizer = Tokenizer(CHARSET)
    transform = T.Compose(
        [
            T.Resize((32, 128), T.InterpolationMode.BICUBIC),
            T.ToTensor(),
            T.Normalize(0.5, 0.5),
        ]
    )

    class CaptchaSplitDataset(Dataset):
        def __init__(self, rows: Sequence[Dict[str, str]], root: Path) -> None:
            self.items: List[Tuple[Path, str]] = []
            for row in rows:
                label = normalize_label(row.get("label", ""))
                rel = (row.get("path") or f"raw/{row.get('filename', '')}").replace("\\", "/")
                img_path = (root / rel).resolve()
                if label and img_path.is_file():
                    self.items.append((img_path, label))

        def __len__(self) -> int:
            return len(self.items)

        def __getitem__(self, idx: int):
            path, label = self.items[idx]
            img = transform(Image.open(path).convert("RGB"))
            return img, label

    def collate(batch):
        images, labels = zip(*batch)
        return torch.stack(images, dim=0), list(labels)

    if args.batch_size != 1:
        print("WARNING: PARSeq JIT only supports batch_size=1; forcing batch_size=1", file=sys.stderr)
        args.batch_size = 1

    train_loader = DataLoader(
        CaptchaSplitDataset(train_rows, dataset_dir),
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate,
        num_workers=0,
    )

    model_path = args.model_path.resolve()
    if not model_path.is_file():
        print(f"ERROR: model not found: {model_path}", file=sys.stderr)
        return 1

    model = torch.jit.load(str(model_path), map_location=device)
    model.eval()
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    best_path = out_dir / "captcha_model_best.pt"
    shutil.copy2(model_path, best_path)

    base_correct, base_total = _accuracy(torch, model, tokenizer, transform, val_rows, dataset_dir, device)
    print(f"Baseline val accuracy: {base_correct}/{base_total} ({100*base_correct/max(1,base_total):.1f}%)", flush=True)

    history: List[Dict[str, float]] = []
    best_val = base_correct / max(1, base_total)

    for epoch in range(1, args.epochs + 1):
        model.eval()
        running = 0.0
        steps = 0
        for images, labels in train_loader:
            images = images.to(device)
            logits = model(images)
            loss = _train_loss(F, tokenizer, logits, labels, device)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            running += float(loss.item())
            steps += 1

        val_correct, val_total = _accuracy(torch, model, tokenizer, transform, val_rows, dataset_dir, device)
        val_acc = val_correct / max(1, val_total)
        avg_loss = running / max(1, steps)
        print(f"epoch {epoch:02d}  loss={avg_loss:.4f}  val={val_correct}/{val_total} ({100*val_acc:.1f}%)", flush=True)
        history.append({"epoch": epoch, "loss": avg_loss, "val_acc": val_acc})

        if val_acc > best_val:
            best_val = val_acc
            torch.jit.save(model, str(best_path))

    final_path = out_dir / "captcha_model_last.pt"
    torch.jit.save(model, str(final_path))

    test_correct, test_total = _accuracy(torch, model, tokenizer, transform, test_rows, dataset_dir, device)
    report = {
        "ok": True,
        "dataset_dir": str(dataset_dir),
        "base_model": str(model_path),
        "out_dir": str(out_dir),
        "train": len(train_rows),
        "val": len(val_rows),
        "test": len(test_rows),
        "epochs": args.epochs,
        "baseline_val_acc": base_correct / max(1, base_total),
        "best_val_acc": best_val,
        "test_acc": test_correct / max(1, test_total),
        "best_model": str(best_path),
        "last_model": str(final_path),
        "history": history,
    }
    (out_dir / "train_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
