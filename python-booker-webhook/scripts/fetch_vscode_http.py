"""Fetch VPS artifacts via code-server HTTP session."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests

BASE = "https://vs-code.chattyai.cloud"
WORKSPACE = "/config/workspace/dir"
ARTIFACT_DIR = f"{WORKSPACE}/python-booker-webhook/artifacts/1655582-turbo"
LOG_PATH = f"{WORKSPACE}/python-booker-webhook/python/debug-ce612b.log"
PASSWORD = "Omarzaki10"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "1655582-vscode-fetch"
OUT.mkdir(parents=True, exist_ok=True)


def extract_captcha_errors(html: str) -> list[str]:
    patterns = [
        r"text from the picture does not match[^<\"]*",
        r"validation-summary-errors[^>]*>.*?</div>",
        r"field-validation-error[^>]*>.*?</span>",
        r"CaptchaText[^<\"]{0,200}",
    ]
    found = []
    for p in patterns:
        for m in re.finditer(p, html, re.I | re.S):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s and len(s) > 5 and s not in found:
                found.append(s[:500])
    return found


def try_fetch_file(session: requests.Session, path: str) -> tuple[int, bytes]:
    encoded = quote(path, safe="")
    urls = [
        f"{BASE}/vscode-remote-resource?path={encoded}",
        f"{BASE}/vscode-remote-resource?path={encoded}&tkn=",
        f"{BASE}/download?path={encoded}",
    ]
    for url in urls:
        try:
            r = session.get(url, timeout=30, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 0:
                if b"<!DOCTYPE html>" in r.content[:200] and b"login" in r.content[:2000].lower():
                    continue
                return r.status_code, r.content
        except Exception:
            pass
    return 404, b""


def main():
    report = {
        "login": "unknown",
        "file_list": [],
        "attempt_01_response": {"exists": False, "captcha_errors": [], "size": 0},
        "attempt_01_captcha_png": {"exists": False, "size": 0, "metadata": ""},
        "log_lines_1655582": [],
        "errors": [],
        "blockers": [],
        "http_attempts": [],
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "artifact-fetch/1.0"})

    try:
        login_r = session.post(
            f"{BASE}/login",
            data={"password": PASSWORD},
            timeout=30,
            allow_redirects=True,
        )
        report["http_attempts"].append({"url": "/login", "status": login_r.status_code})
        if login_r.status_code in (200, 302) and "incorrect" not in login_r.text.lower():
            report["login"] = "success"
        else:
            report["login"] = "failure"
            report["blockers"].append(f"Login HTTP {login_r.status_code}")
            print(json.dumps(report, indent=2))
            return 1

        # Probe index
        idx = session.get(f"{BASE}/?folder={WORKSPACE}", timeout=30)
        report["http_attempts"].append({"url": "/?folder=...", "status": idx.status_code})

        files_to_get = [
            ("attempt_01_response.html", "attempt_01_response"),
            ("attempt_01_captcha.png", "attempt_01_captcha_png"),
            ("attempt_10_response.html", "attempt_10_response"),
            ("01_capture_form.html", "capture_form"),
        ]
        for fname, key in files_to_get:
            fpath = f"{ARTIFACT_DIR}/{fname}"
            status, content = try_fetch_file(session, fpath)
            report["http_attempts"].append({"path": fpath, "status": status, "bytes": len(content)})
            if status == 200 and content:
                out_file = OUT / fname
                out_file.write_bytes(content)
                if fname.endswith(".html"):
                    text = content.decode("utf-8", errors="replace")
                    bucket = report["attempt_01_response"] if "01_response" in fname else {}
                    if "01_response" in fname:
                        report["attempt_01_response"]["exists"] = True
                        report["attempt_01_response"]["size"] = len(content)
                        report["attempt_01_response"]["captcha_errors"] = extract_captcha_errors(text)
                elif fname.endswith(".png"):
                    report["attempt_01_captcha_png"]["exists"] = True
                    report["attempt_01_captcha_png"]["size"] = len(content)
                    report["attempt_01_captcha_png"]["metadata"] = f"PNG {len(content)} bytes"

        # Log file
        status, content = try_fetch_file(session, LOG_PATH)
        report["http_attempts"].append({"path": LOG_PATH, "status": status, "bytes": len(content)})
        if status == 200 and content:
            log_text = content.decode("utf-8", errors="replace")
            keys = ("H_gemini_resp", "H_ocr_resp", "captcha_solved", "pre_submit_dom_state")
            for line in log_text.splitlines():
                if "1655582" in line and any(k in line for k in keys):
                    report["log_lines_1655582"].append(line.strip())
            if not report["log_lines_1655582"]:
                for line in log_text.splitlines():
                    if "1655582" in line:
                        report["log_lines_1655582"].append(line.strip())
                report["log_lines_1655582"] = report["log_lines_1655582"][-80:]
            (OUT / "debug-ce612b.log").write_bytes(content)
        else:
            report["blockers"].append(f"Could not fetch log via HTTP: {LOG_PATH} status={status}")

        if not report["attempt_01_response"]["exists"]:
            report["blockers"].append("attempt_01_response.html not accessible via HTTP API")

    except Exception as e:
        report["errors"].append(f"{type(e).__name__}: {e}")

    (OUT / "report-http.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["login"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
