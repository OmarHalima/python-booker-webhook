#!/usr/bin/env python3
"""Start ngrok for sandbox booker port and print public URL."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.n8n.local"


def load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def set_booker_url(url: str) -> None:
    lines = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("BOOKER_PUBLIC_URL="):
                lines.append(f"BOOKER_PUBLIC_URL={url}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"BOOKER_PUBLIC_URL={url}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_tunnel_url() -> str:
    for port in (4040, 4041):
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/tunnels", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    addr = tunnel.get("config", {}).get("addr", "")
                    if ":8010" in addr or addr.endswith("/8010"):
                        return tunnel.get("public_url", "")
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel.get("public_url", "")
        except (OSError, URLError):
            continue
    return ""


def main() -> int:
    env = load_env()
    port = int(env.get("PYTHON_BOOKER_PORT", "8010"))
    token = env.get("NGROK_AUTHTOKEN", "")
    if not token:
        print("NGROK_AUTHTOKEN missing in .env.n8n.local", file=sys.stderr)
        return 1

    existing = read_tunnel_url()
    if existing:
        set_booker_url(existing)
        print(f"ngrok already running for port {port}")
        print(f"BOOKER_PUBLIC_URL={existing}")
        print("Re-run: python scripts/patch_booker_lock.py && python scripts/n8n_push_booker_lock.py")
        return 0

    subprocess.run(["ngrok", "config", "add-authtoken", token], check=False)
    proc = subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    public_url = ""
    for _ in range(30):
        time.sleep(1)
        public_url = read_tunnel_url()
        if public_url:
            break

    if not public_url:
        proc.terminate()
        print("ngrok started but no HTTPS tunnel found on :4040/:4041", file=sys.stderr)
        return 1

    set_booker_url(public_url)
    print(f"ngrok PID {proc.pid}")
    print(f"BOOKER_PUBLIC_URL={public_url}")
    print("Re-run: python scripts/patch_booker_lock.py && python scripts/n8n_push_booker_lock.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
