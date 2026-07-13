#!/usr/bin/env python3
"""Load sandbox n8n workflow IDs from docs/n8n-workflow-ids.json."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META_PATH = ROOT / "docs" / "n8n-workflow-ids.json"


def load_workflow_ids() -> dict:
    if not META_PATH.exists():
        return {}
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def booker_id(default: str = "CLTCcjVtgfjqyMnF") -> str:
    return (load_workflow_ids().get("booker_lock_sandbox") or {}).get("id") or default


def watcher_kopenhagen_id(default: str = "8o4tnLxWbWKRgfTq") -> str:
    return (load_workflow_ids().get("watcher_kopenhagen_sandbox") or {}).get("id") or default
