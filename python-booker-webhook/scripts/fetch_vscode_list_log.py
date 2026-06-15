import re
from urllib.parse import quote
import requests
from pathlib import Path

BASE = "https://vs-code.chattyai.cloud"
PWD = "Omarzaki10"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "1655582-vscode-fetch"
ARTIFACT_DIR = "/config/workspace/dir/python-booker-webhook/artifacts/1655582-turbo"
LOG_PATHS = [
    "/config/workspace/dir/python-booker-webhook/debug-ce612b.log",
    "/config/workspace/dir/python-booker-webhook/python/debug-ce612b.log",
]

s = requests.Session()
s.post(f"{BASE}/login", data={"password": PWD})

# directory listing via probing known patterns + common names
known = [
    "attempt_01_response.html",
    "attempt_10_response.html",
    "01_capture_form.html",
    "attempt_01_captcha.png",
    "attempt_10_captcha.png",
    "navigation-debug.json",
    "fetch-report.json",
    "pre_submit_dom_state.json",
    "captcha_solved.json",
]
found = []
for name in known:
    p = f"{ARTIFACT_DIR}/{name}"
    r = s.get(f"{BASE}/vscode-remote-resource?path={quote(p, safe='')}", timeout=20)
    if r.status_code == 200 and len(r.content) > 0 and b"login" not in r.content[:500].lower():
        found.append({"name": name, "bytes": len(r.content)})

# brute probe attempt_02..attempt_09
for i in range(2, 10):
    for ext in ("response.html", "captcha.png"):
        name = f"attempt_{i:02d}_{ext}"
        p = f"{ARTIFACT_DIR}/{name}"
        r = s.get(f"{BASE}/vscode-remote-resource?path={quote(p, safe='')}", timeout=10)
        if r.status_code == 200 and len(r.content) > 100:
            found.append({"name": name, "bytes": len(r.content)})

print("FILE_LIST")
for f in sorted(found, key=lambda x: x["name"]):
    print(f"{f['name']}\t{f['bytes']}")

keys = ("H_gemini_resp", "H_ocr_resp", "captcha_solved", "pre_submit_dom_state")
for log_path in LOG_PATHS:
    r = s.get(f"{BASE}/vscode-remote-resource?path={quote(log_path, safe='')}", timeout=30)
    print(f"LOG {log_path} -> {r.status_code} {len(r.content)}")
    if r.status_code == 200 and r.content:
        OUT.joinpath("debug-ce612b.log").write_bytes(r.content)
        lines = [ln for ln in r.text.splitlines() if "1655582" in ln]
        filt = [ln for ln in lines if any(k in ln for k in keys)]
        print("FILTERED", len(filt))
        for ln in filt:
            print(ln[:500])
        if not filt:
            print("BROAD", min(40, len(lines)))
            for ln in lines[-40:]:
                print(ln[:500])
        break
