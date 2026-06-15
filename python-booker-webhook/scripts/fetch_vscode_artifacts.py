"""Fetch run artifacts from remote code-server via Playwright."""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://vs-code.chattyai.cloud"
FOLDER = "/config/workspace/dir/python-booker-webhook/artifacts/1655582-turbo"
LOG_PATHS = [
    "/config/workspace/dir/python-booker-webhook/debug-ce612b.log",
    "/config/workspace/dir/python-booker-webhook/python/debug-ce612b.log",
]
PASSWORD = "Omarzaki10"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "1655582-vscode-fetch"
OUT.mkdir(parents=True, exist_ok=True)


def extract_captcha_errors(html: str) -> list[str]:
    patterns = [
        r"text from the picture does not match[^<]*",
        r"validation-summary-errors[^>]*>.*?</div>",
        r"field-validation-error[^>]*>.*?</span>",
        r"CaptchaText[^<]*",
        r"BDC_VCID_Captcha[^\"']*",
    ]
    found = []
    for p in patterns:
        for m in re.finditer(p, html, re.I | re.S):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s and s not in found:
                found.append(s[:500])
    return found


def main():
    report = {
        "login": "unknown",
        "file_listing": [],
        "attempt_01_response": {"exists": False, "captcha_errors": [], "excerpt": ""},
        "attempt_10_response": {"exists": False, "captcha_errors": [], "excerpt": ""},
        "attempt_01_captcha_png": {"exists": False, "size": 0},
        "log_lines_1655582": [],
        "errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            url = f"{BASE}/?folder=/config/workspace/dir"
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            # code-server password gate
            pwd_input = page.locator('input[type="password"]')
            if pwd_input.count() > 0:
                pwd_input.first.fill(PASSWORD)
                submit = page.locator('button[type="submit"], input[type="submit"]')
                if submit.count() > 0:
                    submit.first.click()
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(5000)
                body = page.content()
                if "incorrect" in body.lower() or "invalid password" in body.lower():
                    report["login"] = "failure"
                    report["errors"].append("Password rejected")
                else:
                    report["login"] = "success"
            else:
                report["login"] = "success (no password prompt)"

            page.wait_for_timeout(5000)
            page.screenshot(path=str(OUT / "01-after-login.png"), full_page=True)

            # Open integrated terminal via keyboard shortcut
            page.keyboard.press("Control+`")
            page.wait_for_timeout(2000)

            def run_terminal(cmd: str, wait_ms: int = 4000) -> str:
                page.keyboard.type(cmd, delay=10)
                page.keyboard.press("Enter")
                page.wait_for_timeout(wait_ms)
                # Try to read terminal output from xterm
                term = page.locator(".xterm-rows, .terminal-output, .monaco-scrollable-element")
                if term.count() > 0:
                    try:
                        return term.first.inner_text(timeout=3000)
                    except Exception:
                        pass
                return page.locator("body").inner_text()[-8000:]

            # List directory
            list_cmd = f"ls -la {FOLDER} 2>&1"
            listing = run_terminal(list_cmd, 5000)
            report["file_listing_raw"] = listing
            for line in listing.splitlines():
                if "1655582" in line or "attempt_" in line or "capture" in line or "captcha" in line:
                    report["file_listing"].append(line.strip())
            if not report["file_listing"]:
                for line in listing.splitlines()[-30:]:
                    if line.strip():
                        report["file_listing"].append(line.strip())

            files_to_fetch = [
                "attempt_01_response.html",
                "attempt_10_response.html",
                "attempt_01_captcha.png",
                "01_capture_form.html",
            ]
            for fname in files_to_fetch:
                fpath = f"{FOLDER}/{fname}"
                if fname.endswith(".png"):
                    run_terminal(f"test -f {fpath} && wc -c < {fpath} || echo MISSING", 3000)
                    out = run_terminal("", 500)
                    if "MISSING" not in out:
                        report["attempt_01_captcha_png"]["exists"] = True
                        m = re.search(r"(\d+)", out)
                        if m:
                            report["attempt_01_captcha_png"]["size"] = int(m.group(1))
                    run_terminal(
                        f"base64 {fpath} 2>/dev/null | head -c 200 || echo PNG_MISSING",
                        4000,
                    )
                    b64_preview = run_terminal("", 500)
                    report["attempt_01_captcha_png"]["base64_preview"] = b64_preview[:200]
                    continue

                run_terminal(f"test -f {fpath} && wc -c < {fpath} || echo MISSING", 3000)
                size_out = run_terminal("", 500)
                key = fname.replace(".html", "").replace("attempt_", "attempt_") + "_response"
                if "attempt_01" in fname:
                    bucket = report["attempt_01_response"]
                elif "attempt_10" in fname:
                    bucket = report["attempt_10_response"]
                else:
                    continue

                if "MISSING" in size_out:
                    bucket["exists"] = False
                    continue

                bucket["exists"] = True
                run_terminal(f"head -c 12000 {fpath}", 5000)
                html = run_terminal("", 500)
                (OUT / fname).write_text(html, encoding="utf-8", errors="replace")
                bucket["excerpt"] = html[:4000]
                bucket["captcha_errors"] = extract_captcha_errors(html)

            # Debug log grep
            for log_path in LOG_PATHS:
                run_terminal(
                    f"grep -n '1655582\\|gemini.*429\\|429\\|ocr source\\|captchaTextMatchesOcr\\|CAPTCHA_FAIL' {log_path} 2>/dev/null | tail -80",
                    6000,
                )
                log_out = run_terminal("", 500)
                if log_out.strip() and "No such file" not in log_out:
                    for line in log_out.splitlines():
                        if any(
                            k in line.lower()
                            for k in (
                                "1655582",
                                "429",
                                "ocr",
                                "captchatextmatchesocr",
                                "captcha_fail",
                                "gemini",
                            )
                        ):
                            report["log_lines_1655582"].append(line.strip())

            page.screenshot(path=str(OUT / "02-final.png"), full_page=True)

        except PWTimeout as e:
            report["errors"].append(f"Timeout: {e}")
        except Exception as e:
            report["errors"].append(f"{type(e).__name__}: {e}")
        finally:
            browser.close()

    out_json = OUT / "report.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["login"].startswith("success") else 1


if __name__ == "__main__":
    sys.exit(main())
