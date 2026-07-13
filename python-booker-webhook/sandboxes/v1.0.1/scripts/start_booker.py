#!/usr/bin/env python3
"""Start v1.0.1 booker on port 8011."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"


def main() -> int:
    print("Starting v1.0.1 uvicorn on http://127.0.0.1:8011 (workers=1)")
    print("Ctrl+C to stop")
    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8011", "--workers", "1"],
        cwd=str(PYTHON_DIR),
    )


if __name__ == "__main__":
    raise SystemExit(main())
