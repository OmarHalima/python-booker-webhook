"""Fetch VPS artifacts via code-server login + terminal (v2)."""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
        r"captcha[^<\"]{0,120}",
    ]
    found = []
    for p in patterns:
        for m in re.finditer(p, html, re.I | re.S):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s and len(s) > 5 and s not in found:
                found.append(s[:500])
    return found


def grep_log_lines(text: str, run_id: str) -> list[str]:
    keys = (
        run_id,
        "H_gemini_resp",
        "H_ocr_resp",
        "captcha_solved",
        "pre_submit_dom_state",
    )
    lines = []
    for line in text.splitlines():
        if run_id in line and any(k in line for k in keys[1:]):
            lines.append(line.strip())
        elif run_id in line:
            lines.append(line.strip())
    return lines


def main():
    report = {
        "login": "unknown",
        "file_list": [],
        "attempt_01_response": {"exists": False, "captcha_errors": [], "size": 0},
        "attempt_01_captcha_png": {"exists": False, "size": 0},
        "log_lines_1655582": [],
        "errors": [],
        "blockers": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)

            pwd = page.locator('input[type="password"]')
            if pwd.count() > 0:
                pwd.first.fill(PASSWORD)
                submit = page.locator('button[type="submit"], input[type="submit"]')
                if submit.count() > 0:
                    submit.first.click()
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                if "incorrect" in page.content().lower():
                    report["login"] = "failure"
                    report["blockers"].append("Password rejected")
                    print(json.dumps(report, indent=2))
                    return 1
                report["login"] = "success"
            else:
                report["login"] = "success (no password prompt)"

            page.goto(
                f"{BASE}/?folder={WORKSPACE}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(8000)

            # Dismiss welcome overlays
            for sel in [
                "button:has-text('Mark Done')",
                "button:has-text('Dismiss')",
                ".monaco-dialog-box button:has-text('Yes')",
            ]:
                btn = page.locator(sel)
                if btn.count() > 0:
                    try:
                        btn.first.click(timeout=2000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

            # Open terminal via command palette (more reliable than Ctrl+`)
            page.keyboard.press("Control+Shift+P")
            page.wait_for_timeout(1000)
            page.keyboard.type("Terminal: Create New Terminal", delay=20)
            page.wait_for_timeout(800)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)

            # Focus terminal textarea
            term_input = page.locator(".xterm-helper-textarea, .xterm textarea")
            if term_input.count() == 0:
                panel = page.locator(".panel .terminal, .terminal-group, [aria-label*='Terminal']")
                if panel.count() > 0:
                    panel.first.click()
                    page.wait_for_timeout(1000)
            if term_input.count() > 0:
                term_input.first.click(force=True)
            page.wait_for_timeout(500)

            def run_cmd(cmd: str, wait_ms: int = 5000) -> str:
                page.keyboard.type(cmd, delay=5)
                page.keyboard.press("Enter")
                page.wait_for_timeout(wait_ms)
                rows = page.locator(".xterm-rows")
                if rows.count() > 0:
                    try:
                        return rows.last.inner_text(timeout=5000)
                    except Exception:
                        pass
                return ""

            # List directory
            listing = run_cmd(f"ls -la {ARTIFACT_DIR}", 6000)
            report["file_list_raw"] = listing
            for line in listing.splitlines():
                line = line.strip()
                if line and not line.startswith("$") and "ls -la" not in line:
                    if re.search(r"attempt_|capture|captcha|\.html|\.png|\.json|total ", line, re.I):
                        report["file_list"].append(line)
            if not report["file_list"]:
                report["file_list"] = [l.strip() for l in listing.splitlines() if l.strip()][-40:]

            # Fetch HTML
            html_path = f"{ARTIFACT_DIR}/attempt_01_response.html"
            size_out = run_cmd(f"wc -c < {html_path} 2>&1 || echo MISSING", 4000)
            if "MISSING" not in size_out and re.search(r"\d+", size_out):
                report["attempt_01_response"]["exists"] = True
                m = re.search(r"(\d+)", size_out)
                if m:
                    report["attempt_01_response"]["size"] = int(m.group(1))
                html_content = run_cmd(f"cat {html_path}", 8000)
                (OUT / "attempt_01_response.html").write_text(
                    html_content, encoding="utf-8", errors="replace"
                )
                report["attempt_01_response"]["captcha_errors"] = extract_captcha_errors(
                    html_content
                )
            else:
                report["blockers"].append(f"attempt_01_response.html not found at {html_path}")

            # PNG metadata
            png_path = f"{ARTIFACT_DIR}/attempt_01_captcha.png"
            png_meta = run_cmd(
                f"file {png_path} 2>&1; wc -c < {png_path} 2>&1; ls -la {png_path} 2>&1",
                5000,
            )
            report["attempt_01_captcha_png"]["raw"] = png_meta
            if "No such file" not in png_meta and "cannot open" not in png_meta.lower():
                report["attempt_01_captcha_png"]["exists"] = True
                m = re.search(r"(\d+)\s+/", png_meta) or re.search(r"(\d{3,})", png_meta)
                if m:
                    report["attempt_01_captcha_png"]["size"] = int(m.group(1))
            else:
                report["blockers"].append(f"attempt_01_captcha.png not found at {png_path}")

            # Debug log grep
            log_grep = run_cmd(
                f"grep -n '1655582' {LOG_PATH} 2>&1 | grep -E 'H_gemini_resp|H_ocr_resp|captcha_solved|pre_submit_dom_state' || true",
                8000,
            )
            if "No such file" in log_grep:
                report["blockers"].append(f"Log not found: {LOG_PATH}")
            else:
                report["log_lines_1655582"] = [
                    l.strip() for l in log_grep.splitlines() if l.strip() and "grep -n" not in l
                ]
                if not report["log_lines_1655582"]:
                    # Broader grep fallback
                    broad = run_cmd(f"grep -n '1655582' {LOG_PATH} 2>&1 | tail -60", 8000)
                    report["log_lines_1655582_broad"] = [
                        l.strip() for l in broad.splitlines() if l.strip() and "grep -n" not in l
                    ]

            page.screenshot(path=str(OUT / "v2-final.png"), full_page=True)

        except PWTimeout as e:
            report["errors"].append(f"Timeout: {e}")
        except Exception as e:
            report["errors"].append(f"{type(e).__name__}: {e}")
        finally:
            browser.close()

    (OUT / "report-v2.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["login"].startswith("success") else 1


if __name__ == "__main__":
    sys.exit(main())
