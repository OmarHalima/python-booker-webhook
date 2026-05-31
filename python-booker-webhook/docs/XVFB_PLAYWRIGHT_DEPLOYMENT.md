# Xvfb + Playwright Chromium (server deployment)

This project launches **Chromium with `headless=False`** so the booking UI behaves like a real desktop browser (captcha, ASP.NET posts, optional manual review). On a Linux VPS there is normally **no physical display**, so you run a **virtual framebuffer** with **Xvfb** and point Chromium at it via **`DISPLAY`**.

This document describes how to run the FastAPI app (`uvicorn`) under Xvfb. It applies to bare metal, systemd, and Docker-style setups where you install `xvfb` in the same environment as the app.

---

## What you need

| Piece | Purpose |
|--------|---------|
| **Python 3.10+** (recommended) | App + Playwright |
| **`pip install -r requirements.txt`** | Dependencies |
| **`playwright install chromium`** | Browser binary |
| **Linux system libraries for Chromium** | On Debian/Ubuntu: `playwright install-deps chromium` (or install Playwright’s documented deps manually) |
| **`xvfb`** package | Virtual X server (`Xvfb` binary) |
| **`CAPTCHA_OCR_BASE_URL`** | VPS OCR captcha service (`POST …/predict`); primary solver |
| **`GEMINI_API_KEY`** | Captcha fallback (when OCR fails) + optional Gemini form fill |

The app reads env from **`.env`** next to `app.py` (via `python-dotenv`) or from the process environment.

---

## Concept

1. Start **Xvfb** on a display number, e.g. **`:99`**.
2. Export **`DISPLAY=:99`** for the process that runs **uvicorn** (and thus Playwright).
3. Keep **`headless=False`** in code (current default) so Chromium uses the X display.

Chromium does not care whether the display is real or virtual; it only needs a valid **`DISPLAY`**.

---

## Option A: One-shot with `xvfb-run` (simplest)

Install packages (Debian/Ubuntu example):

```bash
sudo apt-get update
sudo apt-get install -y xvfb x11-utils
```

From the project directory (after venv + `pip install -r requirements.txt` + `playwright install chromium` + `playwright install-deps chromium`):

```bash
cd /path/to/python-booker-webhook
source .venv/bin/activate

# Runs uvicorn inside a temporary Xvfb (:99 or next free display)
xvfb-run -a --server-args="-screen 0 1280x720x24" \
  uvicorn app:app --host 0.0.0.0 --port 8000
```

Flags:

- **`-a`** — auto-pick a free display (avoids clashes if multiple jobs run).
- **`-screen 0 1280x720x24`** — resolution and depth; match roughly what you use for `record_video_size` if you care about artifacts.

Set secrets before starting:

```bash
export CAPTCHA_OCR_BASE_URL="https://your-ocr-host.ngrok-free.dev"
export GEMINI_API_KEY="your-key"
# or rely on .env in the app directory (see .env.example)
```

---

## Option B: Long-lived Xvfb + uvicorn (systemd-friendly)

### 1) Start Xvfb on a fixed display

```bash
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
```

### 2) Start the app on the same machine/session

```bash
cd /path/to/python-booker-webhook
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

Both processes must see **`DISPLAY=:99`**. In **systemd**, put **`Environment=DISPLAY=:99`** in the uvicorn unit and start Xvfb in a **pre-start** script or a separate **xvfb.service** that runs before your app.

Example **systemd** sketch (adapt paths/users):

**`/etc/systemd/system/xvfb-booker.service`**

```ini
[Unit]
Description=Xvfb for booker webhook
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x720x24 -nolisten tcp
Restart=always

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/booker-webhook.service`**

```ini
[Unit]
Description=Booker FastAPI webhook
After=network.target xvfb-booker.service
Requires=xvfb-booker.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/python-booker-webhook
Environment=DISPLAY=:99
EnvironmentFile=/path/to/python-booker-webhook/.env
ExecStart=/path/to/python-booker-webhook/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb-booker.service
sudo systemctl enable --now booker-webhook.service
```

---

## Docker / Easy Panel (same idea)

In one container:

1. Install **`xvfb`** (and Playwright’s **chromium** deps) in the image.
2. Use an **entrypoint script** that starts **`Xvfb :99 ...` in the background**, exports **`DISPLAY=:99`**, then **`exec uvicorn ...`**.

Example entrypoint fragment:

```bash
#!/bin/sh
set -e
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
exec uvicorn app:app --host 0.0.0.0 --port 8000
```

Ensure the Dockerfile runs **`playwright install chromium`** and **`playwright install-deps chromium`** (or equivalent distro packages) so Chromium actually starts.

---

## Environment variables (reminder)

| Variable | Notes |
|----------|--------|
| **`CAPTCHA_OCR_BASE_URL`** | Required for primary captcha OCR (`/predict`). |
| **`GEMINI_API_KEY`** | Required for captcha fallback when OCR fails. |
| **`GEMINI_MODEL`** | Optional; default in code is `gemini-2.5-flash`. |
| **`GEMINI_FALLBACK_MODEL`** | Optional; used on HTTP 429. |
| **`GEMINI_FILL_FORM`** | `true` / `false` — Gemini may fill empty applicant fields. |
| **`GEMINI_FILL_FORM_FORCE`** | If `true`, forces Gemini on all collected field ids (use with care). |

Do not commit production `.env` into git.

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| **`BrowserType.launch: ... Target page, context or browser has been closed`** or errors mentioning **display** | **`DISPLAY`** unset or Xvfb not running on that display. |
| **Chromium missing shared libraries** | Run **`playwright install-deps chromium`** on that OS image. |
| **Black screenshots / video** | Xvfb screen size too small or GPU not relevant; increase **`-screen`** resolution. |
| **Very slow** | **`slow_mo=80`** in `app.py` slows every action; reduce for server use if stable. |
| **Outbound TLS / clock issues** | VPS time sync (NTP) and firewall allowing `https://appointment.bmeia.gv.at` and Gemini API. |

---

## Security

- **`POST /webhook/booker`** has **no authentication** in the stock code. Put the service **behind a reverse proxy** with auth, VPN, or IP allowlist before exposing it publicly.
- **`sessionCookie`** in JSON is highly sensitive; treat logs and artifacts as confidential.

---

## Headless alternative (no Xvfb)

If you later switch to **`headless=True`**, you do not need Xvfb, but UI/captcha behavior can differ from headed mode. This repo defaults to **headed** for debugging confidence; Xvfb preserves **headed** semantics on a server.

---

## Related files in this repo

- [`app.py`](../app.py) — `chromium.launch(headless=False, slow_mo=80)`, webhook `POST /webhook/booker`
- [`README.md`](../README.md) — local setup and payload shape
- [`requirements.txt`](../requirements.txt) — Python dependencies
