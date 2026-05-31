#!/usr/bin/env python3
"""
Compare DOM captcha screenshot vs HTTP URL fetch bytes.

BotDetect serves a new image on every GET — parallel URL fetch must NOT be used
for solving. Run after captcha pipeline changes; expect dom==url 0/N unless Option B is fixed.

Usage:
  python scripts/compare_captcha_fetch.py
  python scripts/compare_captcha_fetch.py --samples 5 --office KOPENHAGEN
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pixel_diff_pct(a: bytes, b: bytes) -> float:
    try:
        import io
        import numpy as np
        from PIL import Image

        img_a = np.array(Image.open(io.BytesIO(a)).convert("L"), dtype=np.float32)
        img_b = np.array(Image.open(io.BytesIO(b)).convert("L"), dtype=np.float32)
        h, w = min(img_a.shape[0], img_b.shape[0]), min(img_a.shape[1], img_b.shape[1])
        if h == 0 or w == 0:
            return 100.0
        diff = np.abs(img_a[:h, :w] - img_b[:h, :w])
        return float(diff.mean() / 255.0 * 100.0)
    except Exception:
        return 100.0 if a != b else 0.0


def _context_session_cookie(context) -> str:
    parts: List[str] = []
    for c in context.cookies():
        if "bmeia.gv.at" not in (c.get("domain") or ""):
            continue
        parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def compare_samples(
    *,
    office: str,
    calendar_id: str,
    samples: int,
    run_id: str,
) -> Dict[str, Any]:
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    out_dir = booker.ARTIFACTS_DIR / "captcha-fetch-compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "Office": office,
        "CalendarId": calendar_id,
        "Language": "en",
        "PersonCount": "1",
        "currentMonday": "",
        "currentSlot": "",
        "useSchedulerPost": True,
        "_runId": run_id,
        "runId": run_id,
    }

    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(
            base_url=booker.APPOINTMENT_ORIGIN,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        booker._open_landing_page(page, run_id)
        for sel, val in {
            "#Office": office,
            "select[name='Office']": office,
            "#Language": "en",
        }.items():
            booker._set_scheduler_value(page, sel, val)
        booker._submit_next_command(page, run_id, stage="compare_landing_next")
        booker._apply_reservation_calendar_select(page, payload, run_id, lambda _: None)
        booker._submit_next_command(page, run_id, stage="compare_reservation_next")
        page.wait_for_selector("select[name='PersonCount']", timeout=booker.person_count_wait_ms())
        booker._set_scheduler_value(page, "select[name='PersonCount']", "1")
        booker._submit_next_command(page, run_id, stage="compare_person_next")

        for week in range(4):
            if page.locator("input[name='Start']").count() > 0:
                radios = page.locator("input[name='Start']")
                payload["currentMonday"] = page.locator("#Monday").input_value()
                payload["currentSlot"] = radios.first.get_attribute("value") or ""
                break
            booker._submit_next_command(page, run_id, stage=f"compare_week_{week}")
        else:
            raise RuntimeError("No Start radios found during scan for compare_captcha_fetch")

        session_cookie = _context_session_cookie(context)
        payload["sessionCookie"] = session_cookie

        html = booker.post_select_slot(context, payload)
        page.set_content(booker.inject_base_href(html), wait_until="domcontentloaded")

        for i in range(1, samples + 1):
            if i > 1:
                old_vcid = booker.parse_fresh_fields(page.content()).bdc_vcid or ""
                if not booker._reload_botdetect_captcha(page, run_id, old_vcid):
                    page.evaluate(
                        """() => {
                          const link = document.querySelector('#Captcha_ReloadLink');
                          if (link) link.click();
                        }"""
                    )
                    page.wait_for_timeout(1500)
                booker.wait_botdetect_ready(page, settle_ms=150)

            fresh = booker.parse_fresh_fields(page.content())
            url = fresh.captcha_image_url
            if url.startswith("/"):
                url = f"{booker.APPOINTMENT_ORIGIN}{url}"

            # URL fetch "early" (parallel-style: before extra settle)
            url_early = booker.fetch_captcha_bytes(context, url, session_cookie)
            booker.wait_botdetect_ready(page, settle_ms=150)
            dom = booker.capture_captcha_bytes_from_dom(page)
            url_late = booker.fetch_captcha_bytes(context, url, session_cookie)

            prefix = f"sample_{i:02d}"
            (out_dir / f"{prefix}_dom.png").write_bytes(dom)
            (out_dir / f"{prefix}_url_early.png").write_bytes(url_early)
            (out_dir / f"{prefix}_url_late.png").write_bytes(url_late)

            dom_early_match = _sha256(dom) == _sha256(url_early)
            dom_late_match = _sha256(dom) == _sha256(url_late)
            url_early_late_match = _sha256(url_early) == _sha256(url_late)

            results.append(
                {
                    "sample": i,
                    "vcidTail": (fresh.bdc_vcid or "")[-8:],
                    "domEqualsUrlEarly": dom_early_match,
                    "domEqualsUrlLate": dom_late_match,
                    "urlEarlyEqualsUrlLate": url_early_late_match,
                    "pixelDiffDomVsUrlEarlyPct": round(_pixel_diff_pct(dom, url_early), 1),
                    "pixelDiffDomVsUrlLatePct": round(_pixel_diff_pct(dom, url_late), 1),
                }
            )

        browser.close()

    summary = {
        "ok": True,
        "office": office,
        "calendarId": calendar_id,
        "samples": samples,
        "domEqualsUrlEarly": sum(1 for r in results if r["domEqualsUrlEarly"]),
        "domEqualsUrlLate": sum(1 for r in results if r["domEqualsUrlLate"]),
        "urlEarlyEqualsUrlLate": sum(1 for r in results if r["urlEarlyEqualsUrlLate"]),
        "results": results,
        "artifactsDir": str(out_dir),
        "compareMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    (out_dir / "compare_result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DOM vs URL captcha fetch bytes.")
    parser.add_argument("--office", default="KOPENHAGEN")
    parser.add_argument("--calendar-id", default="9677702")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--run-id", default="captcha-fetch-compare")
    args = parser.parse_args()

    summary = compare_samples(
        office=args.office,
        calendar_id=args.calendar_id,
        samples=max(1, args.samples),
        run_id=args.run_id,
    )
    print(json.dumps(summary, indent=2))
    n = summary["samples"]
    if summary["domEqualsUrlEarly"] < n:
        print(
            f"\nWARNING: DOM != URL on {n - summary['domEqualsUrlEarly']}/{n} samples. "
            "Keep CAPTCHA_PARALLEL_FETCH=false for solving.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
