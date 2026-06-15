#!/usr/bin/env python3
"""One-shot full watcher+booker test for loop ticks."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_full_watcher_booker_test.py"


def main() -> int:
    proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ROOT))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
