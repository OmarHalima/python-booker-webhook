#!/usr/bin/env python3
"""Browser UI to verify captcha OCR labels against manifest.csv.

Displays each captcha image with an editable text box (prefilled from OCR).
Use "OCR correct" when the guess matches, or edit the box and "Save label".
Previous / Next browse without saving. Every save writes manifest.csv immediately.

Usage:
  python scripts/review_captcha_labels.py
  python scripts/review_captcha_labels.py --all
  python scripts/review_captcha_labels.py --difficulty hard --port 8765
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from captcha_dataset_lib import (
    default_dataset_dir,
    load_manifest,
    manifest_path,
    normalize_label,
    save_manifest,
    utc_now_iso,
    validate_label,
)

REVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BMEIA Captcha Review</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --border: #2d3a4f;
      --text: #e7ecf3;
      --muted: #8b9cb3;
      --accent: #3b82f6;
      --ok: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      min-height: 100vh;
    }
    header {
      padding: 16px 24px;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
    }
    h1 { font-size: 1.25rem; margin: 0; font-weight: 600; }
    .stats { color: var(--muted); font-size: 0.9rem; }
    .stats strong { color: var(--text); }
    main {
      display: grid;
      grid-template-columns: 280px 1fr;
      min-height: calc(100vh - 65px);
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      aside { max-height: 200px; border-right: none; border-bottom: 1px solid var(--border); }
    }
    aside {
      border-right: 1px solid var(--border);
      overflow-y: auto;
      background: var(--panel);
    }
    .list-item {
      display: block;
      width: 100%;
      text-align: left;
      padding: 10px 14px;
      border: none;
      border-bottom: 1px solid var(--border);
      background: transparent;
      color: var(--text);
      cursor: pointer;
      font-size: 0.85rem;
    }
    .list-item:hover { background: #243044; }
    .list-item.active { background: #2a4060; border-left: 3px solid var(--accent); }
    .list-item.verified { opacity: 0.85; }
    .list-item .id { color: var(--muted); }
    .list-item .badge {
      display: inline-block;
      font-size: 0.7rem;
      padding: 1px 6px;
      border-radius: 4px;
      margin-left: 6px;
    }
    .badge.ok { background: #14532d; color: #86efac; }
    .badge.pending { background: #422006; color: #fcd34d; }
    .badge.hard { background: #450a0a; color: #fca5a5; }
    section {
      padding: 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      width: 100%;
      max-width: 640px;
    }
    .img-wrap {
      background: #fff;
      border-radius: 8px;
      padding: 12px;
      display: flex;
      justify-content: center;
      margin-bottom: 16px;
    }
    img {
      image-rendering: pixelated;
      max-width: 100%;
      max-height: 120px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      color: var(--muted);
      font-size: 0.85rem;
      margin-bottom: 16px;
    }
    .meta span strong { color: var(--text); }
    .status-pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.8rem;
      font-weight: 600;
    }
    .status-pill.verified { background: #14532d; color: #86efac; }
    .status-pill.pending { background: #422006; color: #fcd34d; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 8px; }
    #label {
      width: 100%;
      font-size: 2rem;
      letter-spacing: 0.35em;
      text-transform: uppercase;
      padding: 12px 16px;
      border: 2px solid var(--border);
      border-radius: 8px;
      background: var(--bg);
      color: var(--text);
      font-family: ui-monospace, monospace;
    }
    #label:focus { outline: none; border-color: var(--accent); }
    #label.dirty { border-color: var(--warn); }
    .ocr-ref {
      margin-top: 8px;
      font-size: 0.85rem;
      color: var(--muted);
    }
    .ocr-ref code {
      color: var(--accent);
      font-size: 1rem;
      letter-spacing: 0.15em;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 20px;
    }
    button {
      font-size: 0.95rem;
      padding: 10px 18px;
      border-radius: 8px;
      border: 1px solid var(--border);
      cursor: pointer;
      background: #243044;
      color: var(--text);
    }
    button:hover { filter: brightness(1.1); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.success { background: var(--ok); border-color: var(--ok); color: #fff; }
    button.warn { background: #78350f; border-color: var(--warn); color: #fff; }
    button.danger { background: #450a0a; border-color: var(--bad); color: #fca5a5; }
    button.nav { min-width: 110px; }
    button:disabled { opacity: 0.45; cursor: not-allowed; filter: none; }
    .hint {
      margin-top: 16px;
      font-size: 0.8rem;
      color: var(--muted);
      line-height: 1.5;
    }
    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      padding: 12px 18px;
      border-radius: 8px;
      background: #14532d;
      color: #86efac;
      opacity: 0;
      transition: opacity 0.2s;
      pointer-events: none;
      z-index: 99;
    }
    .toast.show { opacity: 1; }
    .toast.error { background: #450a0a; color: #fca5a5; }
    #empty { text-align: center; padding: 48px; color: var(--muted); }
  </style>
</head>
<body>
  <header>
    <h1>BMEIA Captcha Review</h1>
    <div class="stats" id="stats">Loading…</div>
  </header>
  <main>
    <aside id="sidebar"></aside>
    <section>
      <div id="empty" hidden>No samples match the current filter.</div>
      <div class="card" id="panel" hidden>
        <div class="img-wrap">
          <img id="img" alt="captcha" />
        </div>
        <div class="meta" id="meta"></div>
        <label for="label">Label (edit if OCR is wrong)</label>
        <input id="label" type="text" autocomplete="off" spellcheck="false" maxlength="8" />
        <div class="ocr-ref">OCR guess: <code id="ocr"></code></div>
        <div class="actions">
          <button type="button" class="nav" id="prev">← Previous</button>
          <button type="button" class="nav" id="next">Next →</button>
          <button type="button" class="success" id="accept">✓ OCR correct</button>
          <button type="button" class="primary" id="save">Save label</button>
          <button type="button" class="warn" id="unverify">Mark unverified</button>
          <button type="button" class="danger" id="delete">Delete</button>
        </div>
        <p class="hint">
          <strong>Enter</strong> = OCR correct &nbsp;|&nbsp;
          <strong>Ctrl+Enter</strong> = save edited label &nbsp;|&nbsp;
          <strong>← / →</strong> = previous / next (no save)
        </p>
      </div>
    </section>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    let items = [];
    let index = 0;

    function toast(msg, isError) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.className = 'toast show' + (isError ? ' error' : '');
      setTimeout(() => el.classList.remove('show'), 2200);
    }

    function current() {
      return items[index] || null;
    }

    function labelDirty() {
      const item = current();
      if (!item) return false;
      const val = document.getElementById('label').value.trim().toUpperCase();
      const ocr = (item.ocr_guess || '').toUpperCase();
      return val !== ocr;
    }

    function updateDirtyState() {
      document.getElementById('label').classList.toggle('dirty', labelDirty());
    }

    function escHtml(s) {
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    async function refreshStats() {
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('stats').innerHTML =
        `Verified <strong>${data.verified}</strong> / ${data.total}` +
        ` &nbsp;·&nbsp; Pending <strong>${data.pending}</strong>` +
        (data.filter ? ` &nbsp;·&nbsp; filter: ${data.filter}` : '') +
        (data.showAll ? ' &nbsp;·&nbsp; showing all' : '');
    }

    async function loadList() {
      const res = await fetch('/api/list');
      const data = await res.json();
      items = data.items || [];
      if (index >= items.length) index = Math.max(0, items.length - 1);
      renderSidebar();
      renderItem();
      await refreshStats();
    }

    function renderSidebar() {
      const aside = document.getElementById('sidebar');
      aside.innerHTML = '';
      items.forEach((item, i) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'list-item' + (i === index ? ' active' : '') +
          (item.verified === 'true' ? ' verified' : '');
        const badge = item.verified === 'true'
          ? '<span class="badge ok">ok</span>'
          : '<span class="badge pending">todo</span>';
        const diff = item.difficulty === 'hard'
          ? '<span class="badge hard">hard</span>' : '';
        const ocrText = item.ocr_guess || '—';
        btn.innerHTML =
          `<span class="id">#${escHtml(item.id)}</span> ${escHtml(ocrText)} ${badge}${diff}`;
        btn.onclick = () => { index = i; renderSidebar(); renderItem(); };
        aside.appendChild(btn);
      });
    }

    function renderItem() {
      const item = current();
      const panel = document.getElementById('panel');
      const empty = document.getElementById('empty');
      if (!item) {
        panel.hidden = true;
        empty.hidden = false;
        return;
      }
      panel.hidden = false;
      empty.hidden = true;

      document.getElementById('img').src =
        '/img/' + encodeURIComponent(item.filename) + '?t=' + Date.now();

      const verified = item.verified === 'true';
      const status = verified
        ? `<span class="status-pill verified">Verified: ${escHtml(item.label || '?')}</span>`
        : '<span class="status-pill pending">Not verified</span>';

      document.getElementById('meta').innerHTML =
        `${status}` +
        `<span><strong>#${escHtml(item.id)}</strong> · ${index + 1} / ${items.length}</span>` +
        `<span>difficulty: <strong>${escHtml(item.difficulty || '?')}</strong></span>` +
        `<span>vcid: …${escHtml(item.vcid_tail || '?')}</span>` +
        (item.ocr_was_wrong === 'true' ? '<span>OCR was wrong</span>' : '');

      document.getElementById('ocr').textContent = item.ocr_guess || '(empty — run ocr_prelabel_manifest.py)';
      const hasOcr = !!(item.ocr_guess && item.ocr_guess.trim());
      const acceptBtn = document.getElementById('accept');
      acceptBtn.disabled = !hasOcr;
      acceptBtn.title = hasOcr ? '' : 'No OCR guess — pre-label first or use Save label';
      const prefill = verified && item.label ? item.label : (item.ocr_guess || item.label || '');
      const input = document.getElementById('label');
      input.value = prefill;
      updateDirtyState();
      input.focus();
      input.select();
      renderSidebar();
    }

    async function apiSave(action) {
      const item = current();
      if (!item) return;
      const label = document.getElementById('label').value;
      let res;
      try {
        res = await fetch('/api/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: item.id, action, label })
        });
      } catch (err) {
        toast('Network error: ' + err, true);
        return;
      }
      let data;
      try {
        data = await res.json();
      } catch (err) {
        toast('Server error (' + res.status + ')', true);
        return;
      }
      if (!data.ok) {
        toast(data.error || 'Save failed', true);
        return;
      }
      toast(data.message || 'Saved to manifest.csv');
      await loadList();
    }

    function go(delta) {
      const next = index + delta;
      if (next < 0 || next >= items.length) return;
      index = next;
      renderSidebar();
      renderItem();
    }

    document.getElementById('label').addEventListener('input', updateDirtyState);
    document.getElementById('prev').onclick = () => go(-1);
    document.getElementById('next').onclick = () => go(1);
    document.getElementById('accept').onclick = () => apiSave('accept');
    document.getElementById('save').onclick = () => apiSave('save');
    document.getElementById('unverify').onclick = () => apiSave('unverify');
    document.getElementById('delete').onclick = () => {
      if (confirm('Delete this sample and remove from manifest?')) apiSave('delete');
    };

    document.addEventListener('keydown', (e) => {
      if (!current()) return;
      const tag = (e.target && e.target.tagName) || '';
      if (e.key === 'ArrowLeft' && tag !== 'INPUT') { e.preventDefault(); go(-1); }
      if (e.key === 'ArrowRight' && tag !== 'INPUT') { e.preventDefault(); go(1); }
      if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        if (labelDirty()) apiSave('save');
        else if (document.getElementById('accept').disabled) apiSave('save');
        else apiSave('accept');
      }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        apiSave('save');
      }
    });

    loadList();
  </script>
</body>
</html>
"""


def _image_content_type(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "application/octet-stream"


class ReviewState:
    def __init__(
        self,
        dataset_dir: Path,
        *,
        difficulty_filter: str = "",
        show_all: bool = False,
    ) -> None:
        self.dataset_dir = dataset_dir
        self.manifest = manifest_path(dataset_dir)
        self.difficulty_filter = difficulty_filter.lower().strip()
        self.show_all = show_all
        self.lock = threading.Lock()
        self.rows = load_manifest(self.manifest)

    def reload(self) -> None:
        with self.lock:
            self.rows = load_manifest(self.manifest)

    def _filtered_rows(self) -> List[Dict[str, str]]:
        items = list(self.rows)
        if not self.show_all:
            items = [r for r in items if (r.get("verified") or "").lower() != "true"]
        if self.difficulty_filter:
            items = [r for r in items if (r.get("difficulty") or "").lower() == self.difficulty_filter]
        return items

    def _row_to_item(self, row: Dict[str, str]) -> Dict[str, str]:
        return {
            "id": row.get("id", ""),
            "filename": row.get("filename", ""),
            "path": row.get("path", ""),
            "difficulty": row.get("difficulty", ""),
            "vcid_tail": row.get("vcid_tail", ""),
            "ocr_guess": row.get("ocr_guess", ""),
            "label": row.get("label", ""),
            "verified": row.get("verified", "false"),
            "ocr_was_wrong": row.get("ocr_was_wrong", ""),
        }

    def list_items(self) -> List[Dict[str, str]]:
        if self.show_all:
            rows = list(self.rows)
            if self.difficulty_filter:
                rows = [r for r in rows if (r.get("difficulty") or "").lower() == self.difficulty_filter]
        else:
            rows = self._filtered_rows()
        return [self._row_to_item(r) for r in rows]

    def _pending_count(self) -> int:
        rows = self.rows
        if self.difficulty_filter:
            rows = [
                r for r in rows
                if (r.get("difficulty") or "").lower() == self.difficulty_filter
            ]
        return sum(1 for r in rows if (r.get("verified") or "").lower() != "true")

    def stats(self) -> Dict[str, Any]:
        total = len(self.rows)
        verified = sum(1 for r in self.rows if (r.get("verified") or "").lower() == "true")
        return {
            "total": total,
            "verified": verified,
            "pending": self._pending_count(),
            "filter": self.difficulty_filter,
            "showAll": self.show_all,
        }

    def _find_row(self, row_id: str) -> Optional[Dict[str, str]]:
        for row in self.rows:
            if row.get("id") == str(row_id):
                return row
        return None

    def _image_path_for_filename(self, filename: str) -> Optional[Path]:
        for row in self.rows:
            if row.get("filename") == filename:
                rel = (row.get("path") or f"raw/{filename}").replace("\\", "/")
                candidate = (self.dataset_dir / rel).resolve()
                dataset_root = self.dataset_dir.resolve()
                if candidate.is_file() and dataset_root in candidate.parents:
                    return candidate
        fallback = (self.dataset_dir / "raw" / filename).resolve()
        if fallback.is_file():
            return fallback
        return None

    def save_action(self, row_id: str, action: str, label: str) -> Dict[str, Any]:
        with self.lock:
            self.rows = load_manifest(self.manifest)
            target = self._find_row(row_id)
            if target is None:
                return {"ok": False, "error": "row not found"}

            if action == "delete":
                rel = target.get("path") or f"raw/{target.get('filename', '')}"
                img = self.dataset_dir / rel.replace("\\", "/")
                if img.is_file():
                    img.unlink(missing_ok=True)
                self.rows = [r for r in self.rows if r.get("id") != str(row_id)]
                save_manifest(self.manifest, self.rows)
                return {"ok": True, "message": "Deleted from manifest"}

            if action == "unverify":
                target["verified"] = "false"
                target["label"] = ""
                target["ocr_was_wrong"] = ""
                target["split"] = ""
                save_manifest(self.manifest, self.rows)
                return {"ok": True, "message": "Marked unverified"}

            ocr_guess = normalize_label(target.get("ocr_guess", ""))
            if action == "accept":
                use = ocr_guess
                if not validate_label(use):
                    return {
                        "ok": False,
                        "error": "No valid OCR guess — run ocr_prelabel_manifest.py first",
                    }
            else:
                use = normalize_label(label)
                if not validate_label(use):
                    return {"ok": False, "error": f"Invalid label {use!r} — need 4–8 alphanumeric chars"}

            target["label"] = use
            target["verified"] = "true"
            if not ocr_guess:
                target["ocr_was_wrong"] = "true" if action == "save" else "false"
            else:
                target["ocr_was_wrong"] = "true" if ocr_guess != use else "false"
            target["split"] = ""
            save_manifest(self.manifest, self.rows)

            if action == "accept":
                msg = f"Verified OCR correct: {use}"
            elif ocr_guess != use:
                msg = f"Saved correction: {ocr_guess or '?'} → {use}"
            else:
                msg = f"Saved label: {use}"
            return {"ok": True, "message": msg, "label": use}


def make_handler(state: ReviewState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._html(REVIEW_HTML)
                return
            if parsed.path == "/api/stats":
                state.reload()
                self._json(200, state.stats())
                return
            if parsed.path == "/api/list":
                state.reload()
                self._json(200, {"items": state.list_items()})
                return
            if parsed.path.startswith("/img/"):
                safe_name = Path(parsed.path.split("/img/", 1)[1]).name
                state.reload()
                img_path = state._image_path_for_filename(safe_name)
                if img_path is None:
                    self.send_error(404)
                    return
                data = img_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _image_content_type(data))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/save":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
            result = state.save_action(
                str(payload.get("id", "")),
                str(payload.get("action", "save")),
                str(payload.get("label", "")),
            )
            code = 200 if result.get("ok") else 400
            self._json(code, result)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Review captcha labels in browser.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--difficulty",
        default="",
        choices=["", "easy", "medium", "hard"],
        help="Filter by difficulty",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show verified + unverified (re-edit any sample)",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    manifest = manifest_path(dataset_dir)
    if not manifest.is_file():
        print(f"ERROR: manifest not found: {manifest}", file=sys.stderr)
        return 1

    state = ReviewState(
        dataset_dir,
        difficulty_filter=args.difficulty,
        show_all=args.all,
    )
    handler = make_handler(state)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Review UI: {url}")
    print(f"Dataset:   {dataset_dir}")
    print(f"Manifest:  {manifest}")
    print("Keys: Enter=OCR correct (or save if edited) | Ctrl+Enter=save | Left/Right=navigate")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
