# Changelog — lock-integration-v1 sandbox

## 2026-06-14

### n8n booker lock workflow

- **Lock leak fix:** `Filter Blacklist Slots` + `IF Has Blacklist Slots` + `Build No Slots Response` + `Unlock Lock Early` when no fresh or blacklist slots to book.
- **Lock leak fix:** `Get Next PENDING Student` → `alwaysOutputData: true` so empty applicant queue still routes to `Student Found?` → `Telegram: No Students Left` → loop/unlock.
- **Terminal loop-back:** all result branches wired to `One Slot at a Time` (prior patch; `if name in conn` bug fixed).
- **Verify:** `scripts/verify_no_slots_unlock.py` — POST empty `foundSlots` must leave `lockBusy: false`.

Regenerate and push:

```powershell
python scripts/patch_booker_lock.py
python scripts/n8n_push_booker_lock.py
```

### Python turbo / captcha

- Always OCR DOM captcha image; parallel URL fetch is cache-warm only.
- `wait_botdetect_ready()` returns bool; 3× retry before giving up.
- `fill_captcha_with_hs_guard()` — turbo **does not submit** when `BDC_Hs_Captcha` stays stale.
- DOM screenshot 3× retry before URL fallback.
- `PLAYWRIGHT_REUSE_BROWSER` default **false** in this sandbox copy.
- Saves `attempt_XX_captcha_dom.png` and `attempt_XX_captcha_ocr.png` in run artifacts.

### Test scripts

- `run_five_watcher_bookings.py` — 5 PENDING via watcher→booker; `--watch-sec N` for timed runs.
- `run_five_bookings.py` — 5× direct turbo baseline.
- `load_workflow_ids.py` — IDs from `docs/n8n-workflow-ids.json`.
- `seed_dummy_applicant()` — unique identity per seed; any suffix (not hex-only).

### Verified E2E (KOPENHAGEN)

- Watcher `8o4tnLxWbWKRgfTq` → booker `CLTCcjVtgfjqyMnF` → ngrok → uvicorn `:8010`
- 5/5 COMPLETED via watcher path (~101s)
- 4/5 in 60s watch window (watcher stopped before 5th booked)
