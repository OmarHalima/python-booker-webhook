#!/usr/bin/env python3
"""
Automated test of Tampermonkey Request Injection (bmeiaTurbo.submitTurbo).

Uses Playwright only to load the capture form + session cookie, then submits via
in-browser fetch with simulateHumanCaptchaInput — same logic as tampermonkey-turbo-injector.user.js.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USERSCRIPT_PATH = ROOT / "docs" / "tampermonkey-turbo-injector.user.js"

# submitTurbo + helpers extracted for page.evaluate (no IIFE wrapper).
TURBO_EVAL_JS = """
async (captchaText) => {
  const ORIGIN = 'https://appointment.bmeia.gv.at';

  function simulateHumanCaptchaInput(el, text) {
    if (!el) return;
    el.focus();
    el.value = '';
    for (const ch of String(text || '')) {
      for (const type of ['keydown', 'keypress', 'input', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(type, { key: ch, bubbles: true, cancelable: true }));
      }
      el.value += ch;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.blur();
  }

  function readFreshFormState() {
    const form = document.querySelector('form');
    if (!form) throw new Error('No form on page');
    const val = (sel) => document.querySelector(sel)?.value || '';
    const action = form.getAttribute('action') || '/HomeWeb/AppointmentPassport';
    const url = action.startsWith('http') ? action : ORIGIN + action;
    return {
      url,
      token: val('#Token'),
      startTime: val('#StartTime'),
      bdcVcid: val('#BDC_VCID_Captcha'),
      bdcHs: val('#BDC_Hs_Captcha'),
      bdcSp: val('#BDC_SP_Captcha'),
      bdcBw: val('#BDC_BackWorkaround_Captcha') || '0',
    };
  }

  function buildFormBody(captchaText) {
    const form = document.querySelector('form');
    if (!form) throw new Error('No form');
    const fd = new FormData(form);
    if (captchaText != null) fd.set('CaptchaText', String(captchaText).toUpperCase());
    fd.set('Command', 'Next');
    return new URLSearchParams(fd).toString();
  }

  const captchaEl = document.querySelector('#CaptchaText');
  simulateHumanCaptchaInput(captchaEl, captchaText);
  const fresh = readFreshFormState();
  const body = buildFormBody(captchaText);
  const t0 = performance.now();
  const res = await fetch(fresh.url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Accept: 'text/html,application/xhtml+xml',
    },
    body,
  });
  const html = await res.text();
  const ms = Math.round(performance.now() - t0);
  const success = /Confirmation of reservation/i.test(html);
  const slotTaken = /allocated otherwise in the meantime/i.test(html);
  const alreadyBooked = /already reserved an appointment/i.test(html);
  const captchaFail = /text from the picture does not match/i.test(html);
  return {
    ok: res.ok,
    status: res.status,
    ms,
    resultType: success
      ? 'SUCCESS'
      : alreadyBooked
        ? 'ALREADY_BOOKED'
        : slotTaken
          ? 'SLOT_TAKEN'
          : captchaFail
            ? 'CAPTCHA_FAIL'
            : 'OTHER',
    fresh,
    htmlSnippet: html.slice(0, 800),
  };
}
"""

TURBO_FORM_SUBMIT_JS = """
async (captchaText) => {
  function simulateHumanCaptchaInput(el, text) {
    if (!el) return;
    el.focus();
    el.value = '';
    for (const ch of String(text || '')) {
      for (const type of ['keydown', 'keypress', 'input', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(type, { key: ch, bubbles: true, cancelable: true }));
      }
      el.value += ch;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.blur();
  }
  const captchaEl = document.querySelector('#CaptchaText');
  simulateHumanCaptchaInput(captchaEl, captchaText);
  const form = document.querySelector('form');
  const nextBtn = document.querySelector('#nextButton');
  let cmd = form.querySelector('input[type="hidden"][name="Command"]');
  if (!cmd) {
    cmd = document.createElement('input');
    cmd.type = 'hidden';
    cmd.name = 'Command';
    form.appendChild(cmd);
  }
  cmd.value = 'Next';
  const t0 = performance.now();
  form.requestSubmit(nextBtn || undefined);
  await new Promise((r) => setTimeout(r, 2500));
  const html = document.documentElement.outerHTML;
  const ms = Math.round(performance.now() - t0);
  const success = /Confirmation of reservation/i.test(html);
  const captchaFail = /text from the picture does not match/i.test(html);
  return {
    ms,
    resultType: success ? 'SUCCESS' : captchaFail ? 'CAPTCHA_FAIL' : 'OTHER',
    htmlSnippet: html.slice(0, 800),
  };
}
"""

FILL_APPLICANT_JS = """
(student) => {
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el && v != null) {
      el.value = String(v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };
  set('Lastname', student.Lastname);
  set('Firstname', student.Firstname);
  set('DateOfBirth', student.DateOfBirth);
  set('TraveldocumentNumber', student.TraveldocumentNumber);
  set('Sex', student.Sex);
  set('Street', student.Street);
  set('Postcode', student.Postcode);
  set('City', student.City);
  set('Country', student.Country);
  set('Telephone', student.Telephone);
  set('Email', student.Email);
  set('LastnameAtBirth', student.LastnameAtBirth);
  set('NationalityAtBirth', student.NationalityAtBirth);
  set('CountryOfBirth', student.CountryOfBirth);
  set('PlaceOfBirth', student.PlaceOfBirth);
  set('NationalityForApplication', student.NationalityForApplication);
  set('TraveldocumentDateOfIssue', student.TraveldocumentDateOfIssue);
  set('TraveldocumentValidUntil', student.TraveldocumentValidUntil);
  set('TraveldocumentIssuingAuthority', student.TraveldocumentIssuingAuthority);
  const dsgvo = document.querySelector('#DSGVOAccepted');
  if (dsgvo) {
    dsgvo.checked = true;
    dsgvo.dispatchEvent(new Event('change', { bubbles: true }));
  }
}
"""


def run_inject_test(payload: dict, *, method: str = "fetch") -> dict:
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    run_id = str(payload.get("runId") or "turbo-inject-test")
    run_dir = booker.ARTIFACTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    student = payload.get("student") or {}
    timings: dict = {}
    t_total = time.perf_counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(
            base_url=booker.APPOINTMENT_ORIGIN,
            viewport={"width": 1280, "height": 900},
        )
        booker.set_context_cookies(context, payload.get("sessionCookie") or "")
        page = context.new_page()
        page.goto(f"{booker.APPOINTMENT_ORIGIN}/HomeWeb/Scheduler", wait_until="domcontentloaded")

        t_nav = time.perf_counter()
        booker.navigate_to_capture_form_via_scheduler_post(
            page, context, payload, run_id, run_dir=run_dir
        )
        timings["schedulerNavMs"] = round((time.perf_counter() - t_nav) * 1000, 1)

        # Let BotDetect client script bind BDC_Hs and reveal captcha image.
        try:
            page.wait_for_selector("#Captcha_CaptchaImage", timeout=15000)
            page.wait_for_function(
                """() => {
                  const img = document.querySelector('#Captcha_CaptchaImage');
                  if (!img) return false;
                  const vis = getComputedStyle(img).visibility;
                  return vis !== 'hidden' && img.offsetWidth > 0;
                }""",
                timeout=20000,
            )
        except Exception:
            pass
        page.wait_for_timeout(300)

        dom_start = page.locator("#StartTime").input_value() if page.locator("#StartTime").count() else ""
        (run_dir / "01_capture_dom.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(run_dir / "01_capture_form.png"), full_page=True)

        page.evaluate(FILL_APPLICANT_JS, student)

        t_cap = time.perf_counter()
        fresh = booker.parse_fresh_fields(page.content())
        try:
            captcha_bytes = booker.capture_captcha_bytes_from_dom(page)
        except Exception:
            captcha_bytes = booker.fetch_captcha_bytes(
                context, fresh.captcha_image_url, payload.get("sessionCookie") or ""
            )
        (run_dir / "02_captcha.png").write_bytes(captcha_bytes)
        captcha_text = booker.solve_captcha(
            captcha_bytes, run_id=run_id, attempt=1, vcid=fresh.bdc_vcid
        )
        timings["captchaSolveMs"] = round((time.perf_counter() - t_cap) * 1000, 1)

        dom_hs_before = page.locator("#BDC_Hs_Captcha").input_value() if page.locator("#BDC_Hs_Captcha").count() else ""

        t_submit = time.perf_counter()
        if method == "form_submit":
            booker._fill_captcha_text_like_user(page, captcha_text)
            booker._inject_agent_command_next_for_captcha(page)
            try:
                with page.expect_navigation(timeout=booker.submit_next_navigation_ms()):
                    page.locator("#nextButton").first.click(timeout=booker.submit_next_click_ms())
            except Exception:
                page.evaluate(
                    """() => {
                      const form = document.querySelector('form');
                      const nextBtn = document.querySelector('#nextButton');
                      if (form) form.requestSubmit(nextBtn || undefined);
                    }"""
                )
                page.wait_for_load_state("domcontentloaded", timeout=booker.nav_dom_content_loaded_ms())
            html = page.content()
            ms = round((time.perf_counter() - t_submit) * 1000)
            success = "Confirmation of reservation" in html
            captcha_fail = "text from the picture does not match" in html
            result = {
                "ms": ms,
                "resultType": "SUCCESS" if success else ("CAPTCHA_FAIL" if captcha_fail else "OTHER"),
                "htmlSnippet": html[:800],
                "fresh": {},
            }
            timings["submitMethod"] = "playwright_next_click"
        else:
            result = page.evaluate(TURBO_EVAL_JS, captcha_text)
            timings["submitMethod"] = "fetch"
        timings["injectSubmitMs"] = round((time.perf_counter() - t_submit) * 1000, 1)
        timings["fetchMs"] = result.get("ms", 0)
        timings["totalMs"] = round((time.perf_counter() - t_total) * 1000, 1)

        if result.get("htmlSnippet"):
            (run_dir / "03_inject_response_snippet.html").write_text(
                result["htmlSnippet"], encoding="utf-8"
            )

        browser.close()

    out = {
        "ok": result.get("resultType") == "SUCCESS",
        "resultType": result.get("resultType"),
        "mode": "turbo_inject",
        "runId": run_id,
        "captchaText": captcha_text,
        "requestedSlot": payload.get("currentSlot"),
        "domStartTime": dom_start,
        "freshFromSubmit": result.get("fresh"),
        "bdcHsBeforeSubmit": dom_hs_before[-12:] if dom_hs_before else "",
        "timingsMs": timings,
        "artifactsDir": str(run_dir),
    }
    (run_dir / "result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Tampermonkey turbo inject via Playwright.")
    parser.add_argument("payload", type=Path, help="JSON payload file")
    parser.add_argument(
        "--method",
        choices=("fetch", "form_submit"),
        default="fetch",
        help="fetch = Tampermonkey submitTurbo; form_submit = requestSubmit after human captcha events",
    )
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    out = run_inject_test(payload, method=args.method)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
