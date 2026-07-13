---
name: booker-package-extract
description: Incrementally extracts the booker/ Python package from booker_legacy.py monolith. Use for Phase 4 refactor, moving captcha_ocr.py and scheduler_html.py, adding compatibility shims, without breaking uvicorn app:app on VPS.
---

# Booker package extraction (Phase 4)

## When to use

- Shrinking `booker_legacy.py` (3500+ lines)
- Creating `booker/` package
- Moving `captcha_ocr.py`, `scheduler_html.py`
- Adding unit tests for extracted pure modules

## Constraints

- **Keep** `uvicorn app:app` working — no `src/` layout yet
- **Keep** compatibility shims at old import paths until VPS updated
- **Run** pytest after every extraction step

## Target structure

```
python-booker-webhook/
├── app.py                    # unchanged entry
├── booker/
│   ├── __init__.py
│   ├── captcha_ocr.py        # move first
│   ├── scheduler_html.py     # move first
│   ├── cookies.py            # extract from legacy
│   ├── html_classifiers.py   # is_success_html, classify_post_submit
│   └── legacy.py             # rename booker_legacy.py (last)
├── captcha_ocr.py            # shim: from booker.captcha_ocr import *
└── booker_legacy.py          # shim during migration
```

## Extraction order

1. **captcha_ocr.py** — already isolated, has unit tests
2. **scheduler_html.py** — pure HTML, no Playwright
3. **cookies.py** — `merge_session_cookie_from_set_cookie`, `parse_cookie_header`
4. **html_classifiers.py** — success/slot-taken/already-booked classifiers
5. **legacy.py** — remaining Playwright wizard
6. **turbo.py** — extract from `app.py` when tests stable

## Shim pattern

```python
# captcha_ocr.py at root (temporary)
from booker.captcha_ocr import *  # noqa: F403
```

## Per-step checklist

- [ ] Move module to `booker/`
- [ ] Add shim at old path
- [ ] Move/update tests in `tests/unit/`
- [ ] `pytest tests/unit tests/integration -q`
- [ ] No new code added to monolith — only moves + extracts

## Review

After each step → `booker-domain-reviewer` agent

Rule: `refactor-guardrails.mdc`

## Do not

- Big-bang rename all imports in one commit
- Delete shims before VPS start scripts updated
- Duplicate code into `sandboxes/*/python/`
