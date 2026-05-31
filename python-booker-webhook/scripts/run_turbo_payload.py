#!/usr/bin/env python3
"""CLI: POST a JSON payload to /webhook/booker (or run inline)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run turbo booker locally (no HTTP server).")
    parser.add_argument("payload", type=Path, help="JSON payload file")
    parser.add_argument("--url", default="", help="If set, POST to this URL instead of inline run")
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))

    if args.url:
        import requests

        resp = requests.post(args.url, json=payload, timeout=300)
        print(resp.status_code)
        print(json.dumps(resp.json(), indent=2))
        return 0 if resp.ok else 1

    from app import run_turbo_booking_flow

    out = run_turbo_booking_flow(payload)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
