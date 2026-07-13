"""
Deprecated module — turbo booker is now the primary `app.py`.

Kept for backward-compatible imports and scripts that still reference
`app_turbo:app` or `from app_turbo import run_turbo_booking_flow`.

Prefer:
  uvicorn app:app --host 0.0.0.0 --port 8000
  from app import run_turbo_booking_flow
"""
from app import (  # noqa: F401
    app,
    run_turbo_booking_flow,
    run_turbo_browser_booking_flow,
    run_turbo_http_booking_flow,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
