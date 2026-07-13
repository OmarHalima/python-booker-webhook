---
name: vps-booker-ops
description: Deploys, restarts, and health-checks the Austrian booker and OCR services on VPS 193.203.191.168 via ssh-remote-management MCP. Use for Docker, uvicorn, Xvfb, logs, env changes, and production incidents.
---

# VPS booker operations

## When to use

- Deploy, restart, or inspect booker on VPS
- Playwright asyncio errors, worker count issues
- OCR service on port 8001 unhealthy
- Update production `.env` solver mode

## Connection

```
Host: 193.203.191.168
User: omar
MCP: user-ssh-remote-management (read schema first)
```

## Production layout

| Port | Process |
|------|---------|
| 8000 | `uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1` |
| 8001 | OCR `POST /predict` |

Docs: `docs/XVFB_PLAYWRIGHT_DEPLOYMENT.md`, `docs/AGENT_HANDOFF.md` § VPS

## Pre-flight

- [ ] Read current solver in `.env` (`gemini_first` vs `ocr_with_gemini_fallback`)
- [ ] Confirm `--workers 1` for booker
- [ ] `PLAYWRIGHT_HEADLESS=true` on Linux VPS

## Typical commands (via SSH MCP)

```bash
docker ps | grep -i hermes\|booker
docker logs --tail 100 <booker-container>
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs
```

Adapt to actual container names on host — discover with `docker ps` first.

## Post-deploy verify

1. HTTP 200 on booker docs/health
2. Test OCR URL from booker `.env` (`CAPTCHA_OCR_BASE_URL`)
3. Optional: local `scripts/run_ocr_captcha_api.py` against ngrok URL

## Delegate

Full incident investigation → `vps-booker-operator` agent  
Captcha logic bugs → `captcha-pipeline` skill

## Never

- Change `uvicorn app:app` entry without migration plan
- Commit VPS `.env` to git
