#!/usr/bin/env python3
"""Patch sandbox watcher for KOPENHAGEN + booker-lock-sandbox webhook."""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "workflows" / "watcher-v3.json"
OUT = ROOT / "workflows" / "watcher-kopenhagen-sandbox-v1.json"

KOPENHAGEN_CONFIG = """const CONFIG = {
  Office: 'KOPENHAGEN',
  CalendarId: '9677702',
  PersonCount: '1',
  Language: 'en',
  MaxWeeksToScan: 12
};

return [{ json: CONFIG }];"""

BOOKER_WEBHOOK = "https://n8n.acmeai.crabdance.com/webhook/booker-lock-sandbox"


def main() -> None:
    wf = json.loads(SRC.read_text(encoding="utf-8"))
    wf = copy.deepcopy(wf)
    wf["name"] = "watcher-kopenhagen-sandbox-v1"
    wf.pop("id", None)
    wf.pop("versionId", None)
    wf["active"] = False
    wf.pop("pinData", None)

    for n in wf["nodes"]:
        if n.get("name") == "Config":
            n["parameters"]["jsCode"] = KOPENHAGEN_CONFIG
        if n.get("name") == "Send to Booker Webhook":
            n["parameters"]["url"] = BOOKER_WEBHOOK
            n["parameters"].setdefault("options", {})
            n["parameters"]["options"]["timeout"] = 300000
        if n.get("name") == "Every 60 Seconds":
            n["name"] = "Every 5 Seconds"

    conn = wf["connections"]
    if "Every 60 Seconds" in conn:
        conn["Every 5 Seconds"] = conn.pop("Every 60 Seconds")

    wf["settings"] = {**(wf.get("settings") or {}), "executionOrder": "v1"}
    wf["settings"].pop("binaryMode", None)

    OUT.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
