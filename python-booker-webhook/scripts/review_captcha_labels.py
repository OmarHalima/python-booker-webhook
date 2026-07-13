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
from urllib.parse import parse_qs, urlparse

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
      --bg: #0f1419; --panel: #1a2332; --border: #2d3a4f; --text: #e7ecf3;
      --muted: #8b9cb3; --accent: #3b82f6; --ok: #22c55e; --warn: #f59e0b; --bad: #ef4444;
    }
    * { box-sizing: border-box; }
    body { font-family: "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; min-height: 100vh; }
    header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
    h1 { font-size: 1.25rem; margin: 0; font-weight: 600; }
    .stats { color: var(--muted); font-size: 0.9rem; }
    .stats strong { color: var(--text); }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--panel); }
    .toolbar select, .toolbar input { background: #243044; border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 6px 10px; font-size: 0.85rem; }
    .toolbar button { background: #243044; border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 6px 14px; font-size: 0.85rem; cursor: pointer; }
    main { display: grid; grid-template-columns: 300px 1fr; min-height: calc(100vh - 110px); }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } aside { max-height: 220px; border-right: none; border-bottom: 1px solid var(--border); } }
    aside { border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; }
    .sidebar-meta { padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 0.8rem; color: var(--muted); }
    #list-container { flex: 1; overflow-y: auto; position: relative; }
    .list-item { display: block; width: 100%; text-align: left; padding: 8px 12px; border: none; border-bottom: 1px solid var(--border); background: transparent; color: var(--text); cursor: pointer; font-size: 0.82rem; height: 36px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .list-item:hover { background: #243044; }
    .list-item.active { background: #2a4060; border-left: 3px solid var(--accent); padding-left: 9px; }
    .list-item .id { color: var(--muted); }
    .badge { display: inline-block; font-size: 0.68rem; padding: 1px 5px; border-radius: 4px; margin-left: 4px; }
    .badge.ok { background: #14532d; color: #86efac; }
    .badge.pending { background: #422006; color: #fcd34d; }
    .badge.hard { background: #450a0a; color: #fca5a5; }
    section { padding: 24px; display: flex; flex-direction: column; align-items: center; gap: 20px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 24px; width: 100%; max-width: 640px; }
    .img-wrap { background: #fff; border-radius: 8px; padding: 12px; display: flex; justify-content: center; margin-bottom: 16px; }
    img { image-rendering: pixelated; max-width: 100%; max-height: 120px; }
    .meta { display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--muted); font-size: 0.85rem; margin-bottom: 16px; }
    .meta span strong { color: var(--text); }
    .status-pill { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; }
    .status-pill.verified { background: #14532d; color: #86efac; }
    .status-pill.pending { background: #422006; color: #fcd34d; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 8px; }
    #label { width: 100%; font-size: 2rem; letter-spacing: 0.35em; text-transform: uppercase; padding: 12px 16px; border: 2px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-family: ui-monospace, monospace; }
    #label:focus { outline: none; border-color: var(--accent); }
    #label.dirty { border-color: var(--warn); }
    .ocr-ref { margin-top: 8px; font-size: 0.85rem; color: var(--muted); }
    .ocr-ref code { color: var(--accent); font-size: 1rem; letter-spacing: 0.15em; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
    button { font-size: 0.95rem; padding: 10px 18px; border-radius: 8px; border: 1px solid var(--border); cursor: pointer; background: #243044; color: var(--text); }
    button:hover { filter: brightness(1.1); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.success { background: var(--ok); border-color: var(--ok); color: #fff; }
    button.warn { background: #78350f; border-color: var(--warn); color: #fff; }
    button.danger { background: #450a0a; border-color: var(--bad); color: #fca5a5; }
    button:disabled { opacity: 0.45; cursor: not-allowed; filter: none; }
    .hint { margin-top: 16px; font-size: 0.8rem; color: var(--muted); line-height: 1.5; }
    .toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 18px; border-radius: 8px; background: #14532d; color: #86efac; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 99; }
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
  <div class="toolbar">
    <select id="f-status">
      <option value="pending">Pending</option>
      <option value="verified">Verified</option>
      <option value="all">All</option>
      <option value="empty_ocr">Empty OCR</option>
    </select>
    <select id="f-difficulty">
      <option value="">Any difficulty</option>
      <option value="easy">Easy</option>
      <option value="medium">Medium</option>
      <option value="hard">Hard</option>
    </select>
    <input id="f-jump" placeholder="Jump #id" style="width:80px" />
    <input id="f-search" placeholder="Search OCR/label" style="width:140px" />
    <button id="f-apply">Apply</button>
  </div>
  <main>
    <aside>
      <div class="sidebar-meta" id="sidebar-meta">0 items</div>
      <div id="list-container"></div>
    </aside>
    <section>
      <div id="empty" hidden>No samples match the current filter.</div>
      <div class="card" id="panel" hidden>
        <div class="img-wrap"><img id="img" alt="captcha" /></div>
        <div class="meta" id="meta"></div>
        <label for="label">Label (edit if OCR is wrong)</label>
        <input id="label" type="text" autocomplete="off" spellcheck="false" maxlength="8" />
        <div class="ocr-ref">OCR guess: <code id="ocr"></code></div>
        <div class="actions">
          <button type="button" id="prev">← Previous</button>
          <button type="button" id="next">Next →</button>
          <button type="button" class="success" id="accept">✓ OCR correct</button>
          <button type="button" class="primary" id="save">Save label</button>
          <button type="button" class="warn" id="unverify">Mark unverified</button>
          <button type="button" class="danger" id="delete">Delete</button>
        </div>
        <p class="hint">
          <strong>Enter</strong> = accept or save &nbsp;|&nbsp;
          <strong>Ctrl+Enter</strong> = save edited &nbsp;|&nbsp;
          <strong>Alt+←/→</strong> = prev/next
        </p>
      </div>
    </section>
  </main>
  <div class="toast" id="toast"></div>
  <script>
    const PAGE = 80;
    const WINDOW = 50;
    let items = [];
    let total = 0;
    let index = 0;
    let filters = { status: 'pending', difficulty: '', q: '', jump: '' };

    function toast(msg, err) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.className = 'toast show' + (err ? ' error' : '');
      setTimeout(() => el.classList.remove('show'), 2200);
    }
    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function current() { return items[index] || null; }
    function labelDirty() {
      const item = current();
      if (!item) return false;
      return document.getElementById('label').value.trim().toUpperCase() !== (item.ocr_guess || '').toUpperCase();
    }
    function updateDirty() {
      document.getElementById('label').classList.toggle('dirty', labelDirty());
    }
    function readFilters() {
      filters.status = document.getElementById('f-status').value;
      filters.difficulty = document.getElementById('f-difficulty').value;
      filters.q = document.getElementById('f-search').value.trim();
      filters.jump = document.getElementById('f-jump').value.trim();
    }
    function listQuery(offset) {
      const p = new URLSearchParams({ offset: String(offset), limit: String(PAGE), status: filters.status });
      if (filters.difficulty) p.set('difficulty', filters.difficulty);
      if (filters.q) p.set('q', filters.q);
      if (filters.jump) p.set('jump', filters.jump);
      return '/api/list?' + p.toString();
    }
    async function refreshStats() {
      const data = await (await fetch('/api/stats')).json();
      document.getElementById('stats').innerHTML =
        `Verified <strong>${data.verified}</strong> / ${data.total}` +
        ` &nbsp;·&nbsp; Pending <strong>${data.pending}</strong>`;
    }
    async function loadPage(offset, replace) {
      const res = await fetch(listQuery(offset));
      const data = await res.json();
      total = data.total || 0;
      if (replace) {
        items = data.items || [];
        index = data.jumpIndex >= 0 ? data.jumpIndex : 0;
        if (index >= items.length) index = Math.max(0, items.length - 1);
      } else {
        items = items.concat(data.items || []);
      }
      document.getElementById('sidebar-meta').textContent =
        `${total} items · showing ${items.length}`;
      renderSidebar();
      renderItem();
      await refreshStats();
    }
    async function reloadList() {
      readFilters();
      items = [];
      index = 0;
      await loadPage(0, true);
      if (items.length < total && items.length < PAGE * 3) {
        await loadPage(items.length, false);
      }
    }
    function renderSidebar() {
      const container = document.getElementById('list-container');
      container.innerHTML = '';
      if (!items.length) return;
      const rowH = 36;
      const start = Math.max(0, index - Math.floor(WINDOW / 2));
      const end = Math.min(items.length, start + WINDOW);
      const wrap = document.createElement('div');
      wrap.style.position = 'relative';
      wrap.style.height = (items.length * rowH) + 'px';
      const inner = document.createElement('div');
      inner.style.position = 'absolute';
      inner.style.top = (start * rowH) + 'px';
      inner.style.left = '0'; inner.style.right = '0';
      for (let i = start; i < end; i++) {
        const item = items[i];
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'list-item' + (i === index ? ' active' : '');
        btn.dataset.index = String(i);
        const badge = item.verified === 'true' ? '<span class="badge ok">ok</span>' : '<span class="badge pending">·</span>';
        const hard = item.difficulty === 'hard' ? '<span class="badge hard">H</span>' : '';
        btn.innerHTML = `<span class="id">#${esc(item.id)}</span> ${esc(item.ocr_guess || '—')} ${badge}${hard}`;
        inner.appendChild(btn);
      }
      wrap.appendChild(inner);
      container.appendChild(wrap);
      if (index >= items.length - 10 && items.length < total) {
        loadPage(items.length, false);
      }
    }
    let skipFocus = false;
    function renderItem() {
      const item = current();
      const panel = document.getElementById('panel');
      const empty = document.getElementById('empty');
      if (!item) { panel.hidden = true; empty.hidden = false; return; }
      panel.hidden = false; empty.hidden = true;
      document.getElementById('img').src = '/img/' + encodeURIComponent(item.filename) + '?t=' + Date.now();
      const verified = item.verified === 'true';
      document.getElementById('meta').innerHTML =
        (verified ? `<span class="status-pill verified">Verified: ${esc(item.label || '?')}</span>` : '<span class="status-pill pending">Not verified</span>') +
        `<span><strong>#${esc(item.id)}</strong> · ${index + 1} / ${items.length} (of ${total})</span>` +
        `<span>difficulty: <strong>${esc(item.difficulty || '?')}</strong></span>`;
      document.getElementById('ocr').textContent = item.ocr_guess || '(empty)';
      const hasOcr = !!(item.ocr_guess && item.ocr_guess.trim());
      document.getElementById('accept').disabled = !hasOcr;
      const prefill = verified && item.label ? item.label : (item.ocr_guess || item.label || '');
      const input = document.getElementById('label');
      input.value = prefill;
      updateDirty();
      if (!skipFocus) { input.focus(); input.select(); }
      skipFocus = false;
      renderSidebar();
    }
    function go(delta) {
      const next = index + delta;
      if (next < 0 || next >= items.length) return;
      index = next;
      renderItem();
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
      } catch (e) { toast('Network error', true); return; }
      const data = await res.json();
      if (!data.ok) { toast(data.error || 'Failed', true); return; }
      toast(data.message || 'Saved');
      if (data.stats) {
        document.getElementById('stats').innerHTML =
          `Verified <strong>${data.stats.verified}</strong> / ${data.stats.total}` +
          ` &nbsp;·&nbsp; Pending <strong>${data.stats.pending}</strong>`;
      }
      if (action === 'delete') {
        items.splice(index, 1);
        total = Math.max(0, total - 1);
        if (index >= items.length) index = Math.max(0, items.length - 1);
        renderSidebar(); renderItem();
        return;
      }
      if (data.updatedRow) {
        if (filters.status === 'pending' && data.updatedRow.verified === 'true') {
          items.splice(index, 1);
          total = Math.max(0, total - 1);
          if (index >= items.length) index = Math.max(0, items.length - 1);
        } else {
          items[index] = data.updatedRow;
        }
      }
      renderSidebar(); renderItem();
    }
    document.getElementById('label').addEventListener('input', updateDirty);
    document.getElementById('list-container').addEventListener('mousedown', e => {
      if (e.target.closest('.list-item')) skipFocus = true;
    });
    document.getElementById('list-container').addEventListener('click', e => {
      const btn = e.target.closest('.list-item');
      if (!btn || btn.dataset.index === undefined) return;
      index = parseInt(btn.dataset.index, 10);
      if (Number.isNaN(index)) return;
      renderItem();
    });
    document.querySelector('.actions').addEventListener('mousedown', () => { skipFocus = true; });
    document.getElementById('prev').addEventListener('click', () => go(-1));
    document.getElementById('next').addEventListener('click', () => go(1));
    document.getElementById('accept').addEventListener('click', () => apiSave('accept'));
    document.getElementById('save').addEventListener('click', () => apiSave('save'));
    document.getElementById('unverify').addEventListener('click', () => apiSave('unverify'));
    document.getElementById('delete').addEventListener('click', () => { if (confirm('Delete sample?')) apiSave('delete'); });
    document.getElementById('f-apply').addEventListener('click', () => reloadList());
    document.getElementById('f-jump').addEventListener('keydown', e => { if (e.key === 'Enter') reloadList(); });
    document.getElementById('f-search').addEventListener('keydown', e => { if (e.key === 'Enter') reloadList(); });
    document.addEventListener('keydown', e => {
      if (!current()) return;
      const tag = (e.target && e.target.tagName) || '';
      const inFilter = e.target && (e.target.id === 'f-jump' || e.target.id === 'f-search');
      if (inFilter) return;
      if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); go(-1); return; }
      if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); go(1); return; }
      if (tag !== 'INPUT' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault(); go(e.key === 'ArrowLeft' ? -1 : 1); return;
      }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); apiSave('save'); return; }
      if (e.key === 'Enter' && tag === 'INPUT' && e.target.id === 'label') {
        e.preventDefault();
        if (labelDirty() || document.getElementById('accept').disabled) apiSave('save');
        else apiSave('accept');
      }
    });
    reloadList();
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
        self.lock = threading.RLock()
        self.rows = load_manifest(self.manifest)
        self._mtime = self.manifest.stat().st_mtime if self.manifest.is_file() else 0.0
        self._path_by_filename: Dict[str, Path] = {}
        self._rebuild_path_index()

    def _rebuild_path_index(self) -> None:
        self._path_by_filename = {}
        dataset_root = self.dataset_dir.resolve()
        for row in self.rows:
            filename = row.get("filename", "")
            if not filename:
                continue
            rel = (row.get("path") or f"raw/{filename}").replace("\\", "/")
            candidate = (self.dataset_dir / rel).resolve()
            if candidate.is_file() and dataset_root in candidate.parents:
                self._path_by_filename[filename] = candidate

    def reload(self) -> None:
        with self.lock:
            if not self.manifest.is_file():
                return
            mtime = self.manifest.stat().st_mtime
            if mtime > self._mtime:
                self.rows = load_manifest(self.manifest)
                self._mtime = mtime
                self._rebuild_path_index()

    def _touch_mtime(self) -> None:
        if self.manifest.is_file():
            self._mtime = self.manifest.stat().st_mtime

    def _filtered_rows(self) -> List[Dict[str, str]]:
        items = list(self.rows)
        if not self.show_all:
            items = [r for r in items if (r.get("verified") or "").lower() != "true"]
        if self.difficulty_filter:
            items = [r for r in items if (r.get("difficulty") or "").lower() == self.difficulty_filter]
        return items

    def _apply_ui_filters(
        self,
        rows: List[Dict[str, str]],
        *,
        status: str = "pending",
        difficulty: str = "",
        q: str = "",
    ) -> List[Dict[str, str]]:
        out = list(rows)
        status = (status or "pending").lower()
        if status == "pending":
            out = [r for r in out if (r.get("verified") or "").lower() != "true"]
        elif status == "verified":
            out = [r for r in out if (r.get("verified") or "").lower() == "true"]
        elif status == "empty_ocr":
            out = [r for r in out if not (r.get("ocr_guess") or "").strip()]
        if difficulty:
            out = [r for r in out if (r.get("difficulty") or "").lower() == difficulty.lower()]
        if q:
            needle = q.upper()
            out = [
                r for r in out
                if needle in (r.get("ocr_guess") or "").upper()
                or needle in (r.get("label") or "").upper()
                or needle in (r.get("filename") or "").upper()
                or needle in str(r.get("id") or "")
            ]
        return out

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

    def list_items_page(
        self,
        *,
        offset: int = 0,
        limit: int = 0,
        status: str = "pending",
        difficulty: str = "",
        q: str = "",
        jump: str = "",
    ) -> Dict[str, Any]:
        rows = self._apply_ui_filters(self.rows, status=status, difficulty=difficulty, q=q)
        abs_jump = -1
        if jump:
            for i, row in enumerate(rows):
                if str(row.get("id")) == str(jump):
                    abs_jump = i
                    break
        if limit <= 0:
            page_rows = rows
            eff_offset = 0
            rel_jump = abs_jump
        else:
            eff_offset = max(0, offset)
            if abs_jump >= 0:
                eff_offset = max(0, abs_jump - limit // 2)
            page_rows = rows[eff_offset : eff_offset + limit]
            rel_jump = abs_jump - eff_offset if abs_jump >= 0 else -1
        return {
            "items": [self._row_to_item(r) for r in page_rows],
            "total": len(rows),
            "offset": eff_offset,
            "limit": limit,
            "jumpIndex": rel_jump,
        }

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
        cached = self._path_by_filename.get(filename)
        if cached is not None and cached.is_file():
            return cached
        fallback = (self.dataset_dir / "raw" / filename).resolve()
        if fallback.is_file():
            return fallback
        return None

    def save_action(self, row_id: str, action: str, label: str) -> Dict[str, Any]:
        with self.lock:
            self.reload()
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
                self._touch_mtime()
                self._rebuild_path_index()
                return {"ok": True, "message": "Deleted from manifest", "stats": self.stats()}

            if action == "unverify":
                target["verified"] = "false"
                target["label"] = ""
                target["ocr_was_wrong"] = ""
                target["split"] = ""
                save_manifest(self.manifest, self.rows)
                self._touch_mtime()
                updated = self._row_to_item(target)
                return {
                    "ok": True,
                    "message": "Marked unverified",
                    "updatedRow": updated,
                    "stats": self.stats(),
                }

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
            self._touch_mtime()

            if action == "accept":
                msg = f"Verified OCR correct: {use}"
            elif ocr_guess != use:
                msg = f"Saved correction: {ocr_guess or '?'} → {use}"
            else:
                msg = f"Saved label: {use}"
            return {
                "ok": True,
                "message": msg,
                "label": use,
                "updatedRow": self._row_to_item(target),
                "stats": self.stats(),
            }


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
                params = parse_qs(parsed.query)
                limit_raw = params.get("limit", ["0"])[0]
                offset_raw = params.get("offset", ["0"])[0]
                try:
                    limit = int(limit_raw)
                    offset = int(offset_raw)
                except ValueError:
                    self._json(400, {"ok": False, "error": "invalid offset/limit"})
                    return
                if limit <= 0:
                    self._json(200, {"items": state.list_items(), "total": len(state.list_items())})
                else:
                    page = state.list_items_page(
                        offset=offset,
                        limit=limit,
                        status=params.get("status", ["pending"])[0],
                        difficulty=params.get("difficulty", [""])[0],
                        q=params.get("q", [""])[0],
                        jump=params.get("jump", [""])[0],
                    )
                    self._json(200, page)
                return
            if parsed.path.startswith("/img/"):
                safe_name = Path(parsed.path.split("/img/", 1)[1]).name
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
    print("Keys: Enter=accept/save | Ctrl+Enter=save | Alt+Left/Right=navigate")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
