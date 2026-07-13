#!/usr/bin/env python3
"""Harvest BMEIA BotDetect captcha PNGs for OCR training.

Methods:
  http        Fast: one Playwright session setup, then HTTP GET per image (recommended)
  playwright  Slow: DOM screenshot + BotDetect reload per image

Usage:
  python scripts/harvest_captcha_images.py --count 5000 --method http
  python scripts/harvest_captcha_images.py --count 100 --method playwright --delay-ms 900
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

from captcha_dataset_lib import (
    assess_difficulty,
    default_dataset_dir,
    empty_row,
    known_sha256,
    load_manifest,
    manifest_path,
    next_row_id,
    raw_dir,
    save_manifest,
    sha256_bytes,
    utc_now_iso,
)

load_dotenv(dotenv_path=ROOT / ".env")


def _context_session_cookie(context) -> List[str]:
    parts: List[str] = []
    for c in context.cookies():
        if "bmeia.gv.at" not in (c.get("domain") or ""):
            continue
        parts.append(f"{c['name']}={c['value']}")
    return parts


def _navigate_to_captcha_page(
    *,
    office: str,
    calendar_id: str,
    language: str,
    person_count: str,
    run_id: str,
) -> tuple[Any, Any, Any, Dict[str, Any]]:
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    payload: Dict[str, Any] = {
        "Office": office,
        "CalendarId": calendar_id,
        "Language": language,
        "PersonCount": person_count,
        "currentMonday": "",
        "currentSlot": "",
        "useSchedulerPost": True,
        "_runId": run_id,
        "runId": run_id,
    }

    pw = sync_playwright().start()
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
        "#Language": language,
        "select[name='Language']": language,
    }.items():
        booker._set_scheduler_value(page, sel, val)
    booker._submit_next_command(page, run_id, stage="harvest_landing_next")

    booker._apply_reservation_calendar_select(page, payload, run_id, lambda _: None)
    booker._repair_calendar_id_dom_if_needed(page, payload, run_id)
    booker._submit_next_command(page, run_id, stage="harvest_reservation_next")

    page.wait_for_selector("select[name='PersonCount']", timeout=booker.person_count_wait_ms())
    booker._set_scheduler_value(page, "select[name='PersonCount']", person_count)
    booker._submit_next_command(page, run_id, stage="harvest_person_next")

    for week in range(4):
        if page.locator("input[name='Start']").count() > 0:
            payload["currentMonday"] = page.locator("#Monday").input_value()
            payload["currentSlot"] = page.locator("input[name='Start']").first.get_attribute("value") or ""
            break
        booker._submit_next_command(page, run_id, stage=f"harvest_week_{week}")
    else:
        browser.close()
        pw.stop()
        raise RuntimeError("No Start radios found during harvest navigation")

    payload["sessionCookie"] = "; ".join(_context_session_cookie(context))
    html = booker.post_select_slot(context, payload)
    page.set_content(booker.inject_base_href(html), wait_until="domcontentloaded")
    booker.wait_botdetect_ready(page, settle_ms=150)

    return pw, browser, page, payload


def _reload_captcha(page, run_id: str) -> None:
    import booker_legacy as booker

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


def _save_captcha_row(
    *,
    dataset_dir: Path,
    manifest: Path,
    rows: List[Dict[str, str]],
    seen: set[str],
    row_id: int,
    img_bytes: bytes,
    vcid: str,
    source: str,
) -> tuple[int, bool]:
    digest = sha256_bytes(img_bytes)
    if digest in seen:
        return row_id, False

    difficulty = assess_difficulty(img_bytes)
    filename = f"{row_id:06d}_{vcid[-8:]}_{digest[:8]}.png"
    rel_path = f"raw/{filename}"
    (raw_dir(dataset_dir) / filename).write_bytes(img_bytes)

    row = empty_row(
        id=row_id,
        filename=filename,
        path=rel_path,
        sha256=digest,
        vcid=vcid,
        vcid_tail=vcid[-8:] if vcid else "",
        difficulty=difficulty,
        created_at=utc_now_iso(),
    )
    rows.append(row)
    save_manifest(manifest, rows)
    seen.add(digest)
    print(f"saved {filename} ({difficulty}, {source})")
    return row_id + 1, True


def harvest_http(
    *,
    dataset_dir: Path,
    count: int,
    office: str,
    calendar_id: str,
    language: str,
    person_count: str,
    delay_ms: int,
    run_id: str,
    vcid_refresh_every: int,
    session_refresh_every: int,
    session_cache: Optional[Path] = None,
) -> Dict[str, Any]:
    from captcha_harvest_http import (
        discover_session_playwright,
        harvest_captcha_batch_http,
        load_session_cache,
        save_session_cache,
    )

    out_raw = raw_dir(dataset_dir)
    out_raw.mkdir(parents=True, exist_ok=True)
    manifest = manifest_path(dataset_dir)
    rows = load_manifest(manifest)
    seen = known_sha256(rows)
    row_id = next_row_id(rows)

    saved = 0
    skipped_dup = 0
    t0 = time.perf_counter()

    cache_path = session_cache or (dataset_dir / "session_cache.json")
    payload = load_session_cache(cache_path)
    if payload:
        print(f"loaded session cache from {cache_path}")
    else:
        payload = discover_session_playwright(
            office=office,
            calendar_id=calendar_id,
            language=language,
            person_count=person_count,
            run_id=run_id,
        )
        save_session_cache(cache_path, payload)
        print(f"saved session cache to {cache_path}")

    payload["Language"] = language
    payload["Office"] = office
    payload["CalendarId"] = calendar_id

    def on_image(img_bytes: bytes, vcid: str) -> bool:
        nonlocal row_id, saved, skipped_dup
        row_id, was_new = _save_captcha_row(
            dataset_dir=dataset_dir,
            manifest=manifest,
            rows=rows,
            seen=seen,
            row_id=row_id,
            img_bytes=img_bytes,
            vcid=vcid,
            source="http",
        )
        if was_new:
            saved += 1
            print(f"[{saved}/{count}] vcid …{vcid[-8:]}")
            return True
        skipped_dup += 1
        return False

    def on_vcid_refresh(vcid: str) -> None:
        print(f"vcid refreshed …{vcid[-8:]}")

    def rediscover() -> Dict[str, Any]:
        p = discover_session_playwright(
            office=office,
            calendar_id=calendar_id,
            language=language,
            person_count=person_count,
            run_id=run_id,
        )
        save_session_cache(cache_path, p)
        p["Language"] = language
        p["Office"] = office
        p["CalendarId"] = calendar_id
        return p

    batch = harvest_captcha_batch_http(
        count=count,
        payload=payload,
        delay_ms=delay_ms,
        vcid_refresh_every=vcid_refresh_every,
        session_refresh_every=session_refresh_every,
        on_image=on_image,
        on_vcid_refresh=on_vcid_refresh,
        on_session_refresh=lambda: print("playwright session rediscovery"),
        discover_session=rediscover,
    )

    summary = {
        "ok": saved > 0,
        "method": "http",
        "requested": count,
        "saved": saved,
        "skippedDuplicates": skipped_dup,
        "errors": batch["errors"],
        "vcidRefreshes": batch["vcidRefreshes"],
        "sessionRefreshes": batch["sessionRefreshes"],
        "datasetDir": str(dataset_dir),
        "manifest": str(manifest),
        "sessionCache": str(cache_path),
        "harvestMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    (dataset_dir / "harvest_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def harvest_playwright(
    *,
    dataset_dir: Path,
    count: int,
    office: str,
    calendar_id: str,
    language: str,
    person_count: str,
    delay_ms: int,
    run_id: str,
) -> Dict[str, Any]:
    import booker_legacy as booker

    manifest = manifest_path(dataset_dir)
    rows = load_manifest(manifest)
    seen = known_sha256(rows)
    row_id = next_row_id(rows)

    saved = 0
    skipped_dup = 0
    errors = 0
    t0 = time.perf_counter()

    pw, browser, page, _payload = _navigate_to_captcha_page(
        office=office,
        calendar_id=calendar_id,
        language=language,
        person_count=person_count,
        run_id=run_id,
    )

    try:
        attempts = 0
        max_attempts = max(count * 3, count + 50)
        while saved < count and attempts < max_attempts:
            attempts += 1
            try:
                img_bytes = booker.capture_captcha_bytes_from_dom(page)
                fresh = booker.parse_fresh_fields(page.content())
                vcid = fresh.bdc_vcid or "unknown"

                row_id, was_new = _save_captcha_row(
                    dataset_dir=dataset_dir,
                    manifest=manifest,
                    rows=rows,
                    seen=seen,
                    row_id=row_id,
                    img_bytes=img_bytes,
                    vcid=vcid,
                    source="dom",
                )
                if was_new:
                    saved += 1
                    print(f"[{saved}/{count}] ", end="")
                else:
                    skipped_dup += 1

                if saved < count:
                    _reload_captcha(page, run_id)
                    page.wait_for_timeout(delay_ms)
            except Exception as exc:
                errors += 1
                print(f"harvest error (attempt {attempts}): {exc}", file=sys.stderr)
                page.wait_for_timeout(max(delay_ms, 500))
                if errors > 20 and saved == 0:
                    raise
    finally:
        browser.close()
        pw.stop()

    summary = {
        "ok": saved > 0,
        "method": "playwright",
        "requested": count,
        "saved": saved,
        "skippedDuplicates": skipped_dup,
        "errors": errors,
        "datasetDir": str(dataset_dir),
        "manifest": str(manifest),
        "harvestMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    (dataset_dir / "harvest_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Harvest BMEIA captcha PNGs for OCR training.")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--method", choices=("http", "playwright"), default="http")
    parser.add_argument("--office", default="KOPENHAGEN")
    parser.add_argument("--calendar-id", default="9677702")
    parser.add_argument("--language", default="en")
    parser.add_argument("--person-count", default="1")
    parser.add_argument("--delay-ms", type=int, default=None, help="Delay between fetches (http default 350ms)")
    parser.add_argument(
        "--vcid-refresh-every",
        type=int,
        default=4,
        help="Re-POST scheduler for new captcha id (t=) after N images (http only; BMEIA ~5 GET limit per VCID)",
    )
    parser.add_argument(
        "--session-refresh-every",
        type=int,
        default=400,
        help="Re-run Playwright session discovery after N HTTP fetches (http method only)",
    )
    parser.add_argument(
        "--session-cache",
        type=Path,
        default=None,
        help="JSON file for cached sessionCookie + slot fields (default: <dataset>/session_cache.json)",
    )
    parser.add_argument("--run-id", default="captcha-harvest")
    args = parser.parse_args()

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    delay = 350 if args.delay_ms is None else max(0, args.delay_ms)
    common = dict(
        dataset_dir=dataset_dir,
        count=max(1, args.count),
        office=args.office,
        calendar_id=args.calendar_id,
        language=args.language,
        person_count=args.person_count,
        delay_ms=delay,
        run_id=args.run_id,
    )

    if args.method == "http":
        summary = harvest_http(
            vcid_refresh_every=max(1, args.vcid_refresh_every),
            session_refresh_every=max(50, args.session_refresh_every),
            session_cache=args.session_cache,
            **common,
        )
    else:
        summary = harvest_playwright(
            delay_ms=max(200, delay or 800),
            **common,
        )

    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
