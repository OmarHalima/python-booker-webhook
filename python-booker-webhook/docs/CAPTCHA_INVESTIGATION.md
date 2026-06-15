# BotDetect CAPTCHA Investigation — Deep-Search Findings

**Date:** 2026-05-31  
**Method:** Two `/deep-web-debugger` subagent sessions + live test runs on KOPENHAGEN  
**Status:** Root causes confirmed; production config validated (~6.4s SUCCESS on attempt 1)

---

## Background

After implementing the turbo booking path (scheduler POST + minimal Playwright), persistent
`CAPTCHA_FAIL` on every turbo attempt was observed even though:

- The booking flow itself worked (confirmed by an `ALREADY_BOOKED` result when reusing the same student)
- OCR returned answers that looked visually correct to a human
- The form was being submitted via real browser click (`#nextButton`)

---

## Investigation Round 1 — Initial Deep Search

### Hypotheses going in

| # | Hypothesis | Verdict |
|---|---|---|
| H1 | Parallel captcha fetch via `context.request.get()` causes `greenlet.error` in background thread | ✅ **Confirmed** |
| H2 | `THRESH_BINARY_INV` in preprocessing inverts light-background captchas | ✅ **Confirmed** |
| H3 | `wait_botdetect_ready` returns before image is fully loaded | ✅ **Confirmed** |
| H4 | `CAPTCHA_KEYSTROKE_DELAY_MS=0` starves BotDetect JS handlers | ✅ **Confirmed** |

### Root causes found

**Thread-safety bug (parallel captcha fetch):**  
`fetch_captcha_bytes(context, url, session_cookie)` called `context.request.get()` — a
Playwright API that must run on the Playwright thread — from inside a `ThreadPoolExecutor`
worker. This caused `greenlet.error: Cannot switch to a different thread` on every parallel
fetch, silently falling back to DOM screenshot on all attempts.

**Fix:** replaced `context.request.get()` with `requests.get()` (thread-safe stdlib HTTP).

**`THRESH_BINARY_INV` polarity bug:**  
`enhance_botdetect_image` used `cv2.THRESH_BINARY_INV` unconditionally. BotDetect serves
captchas with both dark backgrounds (mean < 127 → inversion correct) and light backgrounds
(mean ≥ 127 → inversion wrong, turning text white-on-black). For light-background captchas,
enhancement was actively degrading OCR accuracy.

**Fix:** adaptive threshold direction — `THRESH_BINARY_INV` only when `mean < 127`,
otherwise `THRESH_BINARY`.

**`wait_botdetect_ready` fired too early:**  
The readiness check only verified `img.offsetWidth > 0` (visible). The image element can be
visible before the PNG has been fetched. BotDetect's JS removes `disabled` from `#CaptchaText`
inside the image's `load` event — which may not have fired yet.

**Fix:** added `img.complete && img.naturalWidth > 0 && !ct.disabled` to the condition.

**Keystroke delay floor:**  
`CAPTCHA_KEYSTROKE_DELAY_MS=0` caused Playwright to dispatch all keystrokes synchronously
in one JS task, preventing BotDetect's `oninput` handler from updating `BDC_Hs_Captcha`
between characters.

**Fix:** enforced a minimum floor of 20 ms in `_captcha_keystroke_delay_ms()`.

### Changes shipped (Round 1)

| File | Change |
|---|---|
| `booker_legacy.py` | `fetch_captcha_bytes`: `context.request.get` → `requests.get` |
| `booker_legacy.py` | `wait_botdetect_ready`: added `img.complete`, `naturalWidth > 0`, `!ct.disabled` |
| `booker_legacy.py` | `_captcha_keystroke_delay_ms`: 20 ms hard floor |
| `captcha_ocr.py` | `enhance_botdetect_image`: adaptive `THRESH_BINARY` vs `_INV` on mean |
| `app.py` | `pre_submit_dom_state` logging before each submit (`captchaText`, `hsTail`, `vcidTail`) |
| `.env` | `CAPTCHA_KEYSTROKE_DELAY_MS=20`, `BOTDETECT_SETTLE_MS=150` |

### Test result after Round 1

```
turbo-fix-test-0932474 → SUCCESS on attempt 4 (Gemini solver)
  Total time: 17.5s
  captchaParallelUsed: true  ← thread bug fixed, parallel works
```

---

## Solver Strategy Change: Gemini-First

After Round 1 fixes, OCR was still failing on every attempt. A Gemini-first test showed:

```
turbo-gemini1-5284754 → SUCCESS on attempt 2
  Total time: 10.5s  (vs 17.5s with OCR-first)
  captchaText attempt 2: DVWW
```

**Decision:** Switch to **Gemini-first on every attempt, OCR as fallback**. Even though
Gemini takes ~2s per solve vs ~750ms for OCR, fewer retries net a faster overall result.
OCR remains wired as a fallback in case the Gemini API is unavailable.

---

## Investigation Round 2 — Why OCR Fails Despite Correct-Looking Answers

After the user confirmed that OCR answers matched what they saw visually in the captcha
images (`DKC4A4`, `PPPXT`, `DEYRX`, etc.), but the server still rejected every one of them,
a second deep-search session was launched.

### Evidence collected

**`pre_submit_dom_state` log analysis across all runs:**

```
turbo-ocr-raw-5754997 (5 attempts, all CAPTCHA_FAIL):
  attempt 1 → hsTail: 1bfe9a79  ← matches server's initial empty-string Hs exactly!
  attempt 2 → hsTail: 67f9f1c5  ← BotDetect JS did update
  attempt 3 → hsTail: 42244fe8  ← updated
  attempt 4 → hsTail: 19e5227d  ← updated
  attempt 5 → hsTail: 65d987ce  ← updated

turbo-gemini1-5284754 (SUCCESS on attempt 2):
  attempt 1 → hsTail: 4928ff81  ← Gemini also failed on attempt 1
  attempt 2 → hsTail: b120a607  ← SUCCESS
```

**Captcha style analysis:**

| Run | Attempt | Style | Solver | Result |
|---|---|---|---|---|
| turbo-ocr-raw | 1 | Checkered black/white | OCR | FAIL |
| turbo-ocr-raw | 2 | Dark grunge | OCR | FAIL |
| turbo-ocr-raw | 3 | Light lines/noise | OCR | FAIL |
| turbo-ocr-raw | 4 | Very faint/washed out | OCR | FAIL |
| turbo-ocr-raw | 5 | Checkered black/white | OCR | FAIL |
| turbo-gemini1 | 1 | Dark cross-hatch | Gemini | FAIL |
| turbo-gemini1 | 2 | Clean 3D metallic | Gemini | **SUCCESS** |
| turbo-fix-test | 4 | Clean colored pastel | Gemini | **SUCCESS** |

### Root causes found (Round 2)

**Primary: Hard BotDetect styles defeat both OCR and Gemini**

BotDetect randomizes from ~60 visual styles per captcha generation. Some styles are
effectively unsolvable by automated solvers:

- **Checkered / Chess / WavyChess:** 8×8 black-and-white grid overlays text; solvers read
  the grid pattern as glyphs.
- **Dark/Grunge (CaughtInTheNet, SpiderWeb):** near-black background with overlapping lines
  defeats character segmentation.
- **Faint/Washed:** near-zero contrast between text and background.

The stored server answer is a plain alphanumeric string regardless of style. The visual
rendering is a display choice only. This means OCR/Gemini accuracy depends heavily on which
style was randomly served — not on the OCR model's fundamental capability.

**The `BDC_Hs_Captcha` field and what it encodes:**

`BDC_Hs_Captcha` is **not** a hash of the correct answer. From BotDetect's architecture
documentation: correct answers are never persisted client-side. The Hs is:
```
HMAC-SHA1(key, typed_text.toUpperCase())
where key = f(BDC_VCID, BDC_SP)
```
The server independently validates two things:
1. `HMAC(key, submitted_text.upper()) == submitted_Hs` — proof of human interaction
2. `submitted_text.upper() == stored_correct_answer` — the actual captcha text

Wrong answer with valid Hs → fails check 2.  
Correct answer with stale Hs (initial empty-string value) → fails check 1.  
Both return the same error: "The text from the picture does not match."

**Confirmed: Hs race condition on attempt 1**

On the initial form load, `#CaptchaText` starts **enabled** (not disabled). So the
previous `wait_botdetect_ready` condition (`!ct.disabled`) returned `true` immediately,
before BotDetect's `script-include` JS had time to attach its `oninput` listener.

Evidence: `hsTail = "1bfe9a79"` on attempt 1 matches the tail of the server-provided
initial Hs for empty text (`b659535383ff985396246c4c1b98989d1bfe9a79`).

On attempts 2+, the server response renders `CaptchaText` as `disabled="disabled"`,
forcing the readiness check to wait for JS re-enablement — at which point the listener
is wired. This is why attempts 2+ have properly updated Hs values.

**Non-issues (confirmed benign):**

- `remote.captcha.com/include.js` (loaded async) is stats-only per BotDetect documentation.
  It does not touch hidden fields, does not change VCID/Hs/SP, and does not invalidate sessions.
- `BDC_SP_Captcha = 1714899667` is a **random 32-bit server nonce** used in Hs key derivation,
  not a Unix timestamp. The value regenerates per challenge. It does not expire.

**Minor: Captcha text case**

BotDetect's Hs is computed from `typed_text.toUpperCase()`. If we type mixed-case
characters (e.g., `DkC4A4`), the Hs and the submitted text are still consistent
(server computes Hs from uppercase of submitted text). However, typing uppercase
eliminates any potential off-by-one case issue and matches BotDetect's own convention.

### Changes shipped (Round 2)

| File | Change |
|---|---|
| `booker_legacy.py` | `wait_botdetect_ready`: added `typeof ct.Captcha === 'undefined'` check — BotDetect JS attaches `.Captcha` to the input element only after its `oninput` Hs listener is fully registered |
| `booker_legacy.py` | Added `_assess_captcha_difficulty(img_bytes)` — returns `easy`/`medium`/`hard` from brightness + extreme-pixel fraction |
| `booker_legacy.py` | Added `_reload_botdetect_captcha(page, run_id, old_vcid)` — triggers `ct.Captcha.reloadImage()` or reload link, waits for new VCID + `ct.Captcha` presence |
| `booker_legacy.py` | `_fill_captcha_text_like_user`: forces `.upper()` on typed text |
| `app.py` | Style-gate loop after captcha fetch: assess difficulty, reload up to `CAPTCHA_MAX_STYLE_RELOADS` times until `CAPTCHA_MIN_STYLE` threshold is met |
| `.env` / `.env.example` | `CAPTCHA_MAX_STYLE_RELOADS=4`, `CAPTCHA_MIN_STYLE=easy` |

---

## Investigation Round 3 — Parallel URL Fetch Session Mismatch

After Round 2 fixes, a run with style-gate + parallel fetch enabled still failed 5/5
(`turbo-style-gate-5196311`, ~70s total). A follow-up run with parallel fetch disabled
succeeded on attempt 1 in **6.4s** (`turbo-dom-only-2698005`).

### Root cause: HTTP URL fetch returns a *different* captcha than the DOM session

When `CAPTCHA_PARALLEL_FETCH=true`, `fetch_captcha_with_parallel_prefetch()` fetches the
captcha image via HTTP (`requests.get` to `/BotDetectCaptcha.ashx?get=image&c=Captcha&t=<VCID>`)
while the browser waits for BotDetect JS. That HTTP GET can cause BotDetect to serve a
**new rendered image** (often a different visual style) for the same VCID token.

The solver (Gemini) then reads the **URL-fetched bytes**, but the server validates the answer
against the **image the browser actually displayed** in `#Captcha_CaptchaImage`. Even when
Gemini's answer looks correct for the fetched image, the server rejects it with
"text does not match."

Evidence:

| Run | `captchaParallelUsed` | Result | Notes |
|---|---|---|---|
| `turbo-style-gate-5196311` | `true` | CAPTCHA_FAIL 5/5 | Style assessed `easy` on attempts 1 & 3 — still failed |
| `turbo-dom-only-2698005` | `false` (DOM screenshot) | **SUCCESS attempt 1** | 6.4s total |

**Fix (operational):** set `CAPTCHA_PARALLEL_FETCH=false`. Always solve from
`capture_captcha_bytes_from_dom()` — a Playwright screenshot of `#Captcha_CaptchaImage`.

The thread-safety fix (`requests.get` instead of `context.request.get`) remains correct,
but parallel URL fetch should stay **disabled** until the fetch path is proven to return
pixel-identical bytes to the DOM image for the same VCID.

### Style-gate reload: implemented but not reliable yet

Style assessment (`_assess_captcha_difficulty`) correctly labels hard vs easy styles in logs.
However, `_reload_botdetect_captcha()` often logs `reload_failed` because:

- The wait condition requires `BDC_VCID_Captcha` to change after reload.
- BotDetect's in-page reload may refresh the image **without** changing VCID in some cases.
- Each failed reload waits up to **15s** before timing out, adding ~15s per hard captcha attempt.

**Recommended until reload logic is fixed:** `CAPTCHA_MAX_STYLE_RELOADS=0` (assessment-only;
no reload attempts). Style assessment still logs `H_captcha_style` for debugging.

### Test result after Round 3 (production config)

```
turbo-dom-only-2698005 → SUCCESS on attempt 1
  Total time: 6.4s
  captchaParallelUsed: false   ← DOM screenshot only
  captchaText: 55USEC
  Solver: Gemini-first
  Hs race fix: attempt 1 succeeded (no stale hsTail)
```

---

## How the Style-Gate Works

```
for each attempt:
  1. fetch_captcha_with_parallel_prefetch()
  2. for up to CAPTCHA_MAX_STYLE_RELOADS reloads:
       difficulty = _assess_captcha_difficulty(captcha_bytes)
       if difficulty meets CAPTCHA_MIN_STYLE threshold → break
       _reload_botdetect_captcha(page, run_id, old_vcid)   ← uses ct.Captcha.reloadImage()
       fetch new captcha bytes from DOM
  3. solve with Gemini (→ OCR fallback)
  4. type + submit
```

`_assess_captcha_difficulty` uses pixel statistics:
- `mean_brightness < 65` → hard (dark/grunge)
- `>55% of pixels are near-black or near-white` → hard (checkered)
- `mean_brightness < 120 and >35% extreme pixels` → medium
- otherwise → easy

---

## Confirmed Hypotheses Summary

| Hypothesis | Status | Fix |
|---|---|---|
| Parallel fetch thread bug (`context.request` in executor) | ✅ Root cause | Replace with `requests.get()` |
| `THRESH_BINARY_INV` inverts light-background captchas | ✅ Root cause | Adaptive threshold direction |
| `wait_botdetect_ready` fires before image fully loaded | ✅ Root cause | `img.complete && naturalWidth > 0` |
| `CAPTCHA_KEYSTROKE_DELAY_MS=0` starves JS handlers | ✅ Root cause | 20 ms floor |
| Hs race: attempt 1 listener not attached before typing | ✅ Root cause | `typeof ct.Captcha !== 'undefined'` check |
| Hard BotDetect styles defeat OCR and Gemini | ✅ Contributing factor | Style assessment; reload loop not reliable yet |
| Parallel URL fetch returns wrong image for session | ✅ Root cause | `CAPTCHA_PARALLEL_FETCH=false`; use DOM screenshot |
| Style-gate reload gets new easy captcha | ⚠️ Partial | Assessment works; VCID-based reload often times out |
| `remote.captcha.com/include.js` invalidates session | ❌ Disproved | Stats-only script |
| `BDC_SP_Captcha` is a stale 2024 timestamp | ❌ Disproved | Random 32-bit nonce |
| VCID changes between DOM fetch and submit | ❌ Disproved | VCID stable within one attempt |

---

## Test Results Timeline

| Run ID | Solver | Attempt | Result | Total time | Notes |
|---|---|---|---|---|---|
| `turbo-live-test-warm-001` | OCR | 5/5 | CAPTCHA_FAIL | 18.2s | Before fixes; parallel fetch broken (greenlet) |
| `turbo-live-test-warm-002` | OCR | 1/1 | ALREADY_BOOKED | 5.3s | Turbo path works; student was already registered |
| `turbo-live-test-warm-003` | OCR | 5/5 | CAPTCHA_FAIL | 19.2s | Parallel disabled; pure OCR accuracy issue |
| `turbo-fix-test-0932474` | Gemini | 4 | **SUCCESS** | 17.5s | Round 1 fixes applied; parallel now works |
| `turbo-gemini1-5284754` | Gemini | 2 | **SUCCESS** | 10.5s | Gemini-first strategy |
| `turbo-ocr-raw-5754997` | OCR | 5/5 | CAPTCHA_FAIL | 17.0s | Raw OCR confirmed unreliable on hard styles |
| `turbo-style-gate-5196311` | Gemini | 5/5 | CAPTCHA_FAIL | 70.0s | Parallel URL fetch + reload timeouts |
| `turbo-dom-only-2698005` | Gemini | 1 | **SUCCESS** | **6.4s** | DOM screenshot only; best result |

---

## Current State (production config)

| Setting | Value | Why |
|---|---|---|
| Solver | **OCR first, Gemini fallback** | `ocr_with_gemini_fallback`; faster when OCR hits |
| Captcha bytes source | DOM screenshot | Must match browser-displayed image |
| `CAPTCHA_PARALLEL_FETCH` | **`false`** | Empirical 0/5 DOM vs URL match — see below |
| `CAPTCHA_MAX_STYLE_RELOADS` | **`0`** (recommended) | Reload wait logic not reliable; avoid 15s timeouts |
| Hs race fix | `typeof ct.Captcha !== 'undefined'` | Attempt 1 no longer submits stale Hs |
| Keystroke delay | 20 ms minimum | BotDetect `oninput` Hs updates between chars |
| Enhancement | `CAPTCHA_OCR_ENHANCE=false` | Raw images work better for solvers |
| Warm browser | `PLAYWRIGHT_REUSE_BROWSER=true` | Saves launch overhead on retries |

**Expected booking time:** ~5–12s (best ~5.8s OCR / ~6.4s Gemini on attempt 1).

**Code default:** `captcha_parallel_fetch_enabled()` defaults to **`false`** if env unset.

### Empirical parallel-fetch proof (`scripts/compare_captcha_fetch.py`)

| Metric | Result |
|--------|--------|
| DOM vs URL (early) | **0/5 match** |
| DOM vs URL (late) | **0/5 match** |
| URL early vs URL late | **0/5 match** |
| Pixel diff DOM vs URL | **81–99%** |

BotDetect serves a **new captcha on every HTTP GET**. Parallel URL fetch must not be used for solving until Option B is implemented (always solve from DOM; URL prefetch timing-only).

---

## Remaining Open Items

1. **Option B — parallel fetch fix (future):** always return DOM bytes for solving; re-run `compare_captcha_fetch.py` until 5/5 match before re-enabling parallel.
2. **Fix style-gate reload** — wait for image change instead of VCID-only.
3. **Custom trained CRNN/CNN model** — training data on SUCCESS in `artifacts/<runId>-turbo/solved_captchas/`.
4. **Style-gate threshold tuning**
5. **VPS OCR desync / wrong-answer pipeline (2026-05-31):** `ocr_with_gemini_fallback` does not call Gemini on wrong OCR text; VPS run `1411526` failed 10× OCR; user reports OCR answer ≠ artifact PNG (possible n8n retry overwrite, OCR `:8001` cross-talk, or post-fail HTML vs pre-submit PNG confusion). **Production VPS uses `gemini_first`** until fixed — see [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md).
6. **Captcha byte provenance:** log SHA256 + unique OCR filenames; avoid artifact overwrite on n8n retry (same `runId`).
7. **Gemini fallback on CAPTCHA_FAIL** (not only OCR exception) or Gemini on `difficulty == hard`.
8. **Gemini API rate limiting**
