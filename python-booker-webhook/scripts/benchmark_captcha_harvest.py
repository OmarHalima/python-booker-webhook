#!/usr/bin/env python3
"""Benchmark captcha harvest methods (test-driven throughput comparison).

Compares Playwright DOM+reload vs HTTP burst after one session setup.

Usage:
  python scripts/benchmark_captcha_harvest.py --samples 30
  python scripts/benchmark_captcha_harvest.py --samples 20 --http-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT / ".env")


def _benchmark_playwright_dom(
    *,
    samples: int,
    delay_ms: int,
    payload: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    latencies: List[float] = []
    unique_sha: set[str] = set()
    errors = 0
    t_total = time.perf_counter()
    t_setup = time.perf_counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(base_url=booker.APPOINTMENT_ORIGIN)
        page = context.new_page()
        html = booker.post_select_slot(context, {**payload, "useSchedulerPost": True})
        page.set_content(booker.inject_base_href(html), wait_until="domcontentloaded")
        booker.wait_botdetect_ready(page, settle_ms=150)
        setup_ms = (time.perf_counter() - t_setup) * 1000

        t_loop = time.perf_counter()
        for i in range(samples):
            try:
                t0 = time.perf_counter()
                data = booker.capture_captcha_bytes_from_dom(page)
                latencies.append((time.perf_counter() - t0) * 1000)
                import hashlib

                unique_sha.add(hashlib.sha256(data).hexdigest())
                if i + 1 < samples:
                    old_vcid = booker.parse_fresh_fields(page.content()).bdc_vcid or ""
                    if not booker._reload_botdetect_captcha(page, run_id, old_vcid):
                        page.evaluate(
                            "() => { const l = document.querySelector('#Captcha_ReloadLink'); if (l) l.click(); }"
                        )
                        page.wait_for_timeout(1500)
                    booker.wait_botdetect_ready(page, settle_ms=150)
                    if delay_ms > 0:
                        page.wait_for_timeout(delay_ms)
            except Exception:
                errors += 1
        browser.close()

    loop_ms = (time.perf_counter() - t_loop) * 1000
    ok = len(latencies)
    return {
        "method": "playwright_dom_reload",
        "samples": samples,
        "ok": ok,
        "errors": errors,
        "setupMs": round(setup_ms, 1),
        "loopMs": round(loop_ms, 1),
        "totalMs": round((time.perf_counter() - t_total) * 1000, 1),
        "msPerImage": round(loop_ms / ok, 1) if ok else None,
        "imagesPerSec": round(ok / (loop_ms / 1000.0), 2) if loop_ms > 0 and ok else 0,
        "uniqueImages": len(unique_sha),
        "duplicateRate": round(1 - len(unique_sha) / ok, 3) if ok else 0,
        "delayMs": delay_ms,
    }


def run_benchmark(
    *,
    samples: int,
    delay_ms: int,
    office: str,
    calendar_id: str,
    http_only: bool,
    run_id: str,
) -> Dict[str, Any]:
    from captcha_harvest_http import (
        benchmark_method_http_burst,
        discover_session_playwright,
    )

    t0 = time.perf_counter()
    payload = discover_session_playwright(
        office=office,
        calendar_id=calendar_id,
        language="en",
        person_count="1",
        run_id=run_id,
    )
    discover_ms = round((time.perf_counter() - t0) * 1000, 1)

    results: List[Dict[str, Any]] = []

    http_result = benchmark_method_http_burst(
        samples=samples,
        delay_ms=delay_ms,
        payload=payload,
    )
    http_result["discoverMs"] = discover_ms
    results.append(http_result)

    if not http_only:
        dom_result = _benchmark_playwright_dom(
            samples=samples,
            delay_ms=delay_ms,
            payload=payload,
            run_id=run_id,
        )
        dom_result["discoverMs"] = discover_ms
        results.append(dom_result)

    winner = max(results, key=lambda r: r.get("imagesPerSec") or 0)
    summary = {
        "samplesPerMethod": samples,
        "delayMs": delay_ms,
        "discoverMs": discover_ms,
        "winner": winner["method"],
        "results": results,
        "recommendation": (
            "Use --method http on harvest_captcha_images.py for bulk collection. "
            "HTTP GET BotDetectCaptcha.ashx returns a new image each request; "
            "no Playwright reload per image."
        ),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark captcha harvest throughput.")
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument("--delay-ms", type=int, default=0, help="Delay between fetches (HTTP) or reloads (DOM)")
    parser.add_argument("--office", default="KOPENHAGEN")
    parser.add_argument("--calendar-id", default="9677702")
    parser.add_argument("--http-only", action="store_true", help="Skip slow Playwright DOM benchmark")
    parser.add_argument("--run-id", default="captcha-harvest-bench")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    summary = run_benchmark(
        samples=max(3, args.samples),
        delay_ms=max(0, args.delay_ms),
        office=args.office,
        calendar_id=args.calendar_id,
        http_only=args.http_only,
        run_id=args.run_id,
    )

    out = args.out or (ROOT / "datasets" / "bmeia-captcha-v1" / "benchmark_harvest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nReport -> {out}")
    for row in summary["results"]:
        print(
            f"  {row['method']}: {row.get('imagesPerSec', 0)}/s, "
            f"{row.get('msPerImage')} ms/img, unique={row.get('uniqueImages')}/{row.get('ok')}"
        )
    print(f"  WINNER: {summary['winner']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
