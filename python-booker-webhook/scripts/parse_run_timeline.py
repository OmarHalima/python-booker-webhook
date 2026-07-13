"""
Per-runId timeline extractor for the turbo booker debug log (JSONL, dlog() schema).

Builds, for each runId: webhook_entry -> global lock wait/hold -> captcha
attempts (style difficulty, solver source, captcha round) -> final resultType
/ totalMs, and flags common failure signatures so you don't have to
hand-correlate timestamps:

  LOCK_LEAK      global_flock_acquired with no matching global_flock_released
  NO_COMPLETION  turbo flow dispatched but no turbo_browser_done was ever
                 logged (process died mid-request / crashed / OOM)
  OVERLAP        this run's active window overlaps another run's window
                 while both hold/want the global lock (concurrent executions
                 piling up behind BOOKING_USE_GLOBAL_LOCK)

Usage:
  python scripts/parse_run_timeline.py debug-ce612b.log
  python scripts/parse_run_timeline.py debug-ce612b.log --since 1783906000000
  python scripts/parse_run_timeline.py debug-ce612b.log --run-id 2468617
  python scripts/parse_run_timeline.py debug-ce612b.log --anomalies-only
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Attempt:
    attempt: int
    difficulty: str | None = None
    style_reload: int | None = None
    captcha_round: int | None = None
    solver_source: str | None = None
    submit_ms: float | None = None
    submit_url: str | None = None


@dataclass
class RunTimeline:
    run_id: str
    first_ts: int
    last_ts: int
    webhook_entries: int = 0
    lock_wait_ts: int | None = None
    lock_acquired_ts: int | None = None
    lock_released_ts: int | None = None
    dispatch_seen: bool = False
    attempts: dict[int, Attempt] = field(default_factory=dict)
    result_type: str | None = None
    ok: bool | None = None
    total_ms: float | None = None
    other_flags: list[str] = field(default_factory=list)

    def attempt(self, n: int) -> Attempt:
        return self.attempts.setdefault(n, Attempt(attempt=n))

    @property
    def lock_wait_ms(self) -> float | None:
        if self.lock_wait_ts is not None and self.lock_acquired_ts is not None:
            return self.lock_acquired_ts - self.lock_wait_ts
        return None

    @property
    def lock_hold_ms(self) -> float | None:
        if self.lock_acquired_ts is not None and self.lock_released_ts is not None:
            return self.lock_released_ts - self.lock_acquired_ts
        return None

    @property
    def leaked_lock(self) -> bool:
        return self.lock_acquired_ts is not None and self.lock_released_ts is None

    @property
    def has_activity(self) -> bool:
        """True if this run actually entered the booking flow (dispatched,
        acquired the lock, or produced at least one captcha attempt) -- as
        opposed to being a bare bookkeeping bucket like 'no-runid'."""
        return self.dispatch_seen or self.lock_acquired_ts is not None or bool(self.attempts)

    @property
    def no_completion(self) -> bool:
        return self.has_activity and self.result_type is None

    @property
    def active_window(self) -> tuple[int, int]:
        """Window used for lock-contention overlap detection: prefer the
        precise lock wait/release span, fall back to first/last event ts
        when the run never touched the global lock (e.g. local dev with
        BOOKING_USE_GLOBAL_LOCK=false)."""
        start = self.lock_wait_ts if self.lock_wait_ts is not None else self.first_ts
        end = self.lock_released_ts if self.lock_released_ts is not None else self.last_ts
        return start, end


def load_entries(path: Path, since: int | None, until: int | None) -> list[dict]:
    entries: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp")
            if since is not None and (ts is None or ts < since):
                continue
            if until is not None and (ts is None or ts > until):
                continue
            entries.append(entry)
    return entries


def build_timelines(entries: list[dict]) -> dict[str, RunTimeline]:
    timelines: dict[str, RunTimeline] = {}

    def tl(run_id: str, ts: int) -> RunTimeline:
        existing = timelines.get(run_id)
        if existing is None:
            existing = RunTimeline(run_id=run_id, first_ts=ts, last_ts=ts)
            timelines[run_id] = existing
        existing.last_ts = max(existing.last_ts, ts)
        return existing

    for e in entries:
        run_id = e.get("runId") or "no-runid"
        ts = e.get("timestamp", 0)
        msg = e.get("message", "")
        data = e.get("data", {}) or {}
        run = tl(run_id, ts)

        if msg == "webhook_entry":
            run.webhook_entries += 1
        elif msg == "waiting_global_flock":
            run.lock_wait_ts = ts
        elif msg == "global_flock_acquired":
            run.lock_acquired_ts = ts
        elif msg == "global_flock_released":
            run.lock_released_ts = ts
        elif msg == "dispatch":
            run.dispatch_seen = True
        elif msg == "style_assessment":
            a = run.attempt(int(data.get("attempt", 0)))
            a.difficulty = data.get("difficulty")
            a.style_reload = data.get("styleReload")
        elif msg == "captcha_solved":
            a = run.attempt(int(data.get("attempt", 0)))
            a.solver_source = data.get("source")
            a.captcha_round = data.get("captchaRound")
        elif msg == "submit_result":
            a = run.attempt(int(data.get("attempt", 0)))
            a.submit_ms = data.get("submitMs")
            a.submit_url = data.get("url")
        elif msg == "turbo_browser_done":
            run.ok = data.get("ok")
            run.result_type = data.get("resultType")
            run.total_ms = data.get("totalMs")
        elif msg in ("slot_wait_timeout", "global_flock_timeout"):
            run.other_flags.append(msg)

    return timelines


def find_overlaps(timelines: list[RunTimeline]) -> dict[str, list[str]]:
    """Flag real runs (dispatched or lock-holding, excluding the 'no-runid'
    bookkeeping bucket) whose active window overlaps another such run --
    the signature of concurrent watcher executions piling up behind the
    single global lock."""
    overlaps: dict[str, list[str]] = {}
    candidates = [r for r in timelines if r.has_activity and r.run_id != "no-runid"]
    windows = {r.run_id: r.active_window for r in candidates}
    ordered = sorted(candidates, key=lambda r: windows[r.run_id][0])
    for i, a in enumerate(ordered):
        a_start, a_end = windows[a.run_id]
        for b in ordered[i + 1:]:
            b_start, b_end = windows[b.run_id]
            if b_start >= a_end:
                break
            if a_start < b_end:  # strict interval intersection
                overlaps.setdefault(a.run_id, []).append(b.run_id)
                overlaps.setdefault(b.run_id, []).append(a.run_id)
    return overlaps


def format_run(run: RunTimeline, overlap_partners: list[str]) -> str:
    lines = [f"runId={run.run_id} ts=[{run.first_ts}..{run.last_ts}] webhookEntries={run.webhook_entries}"]

    if run.lock_wait_ms is not None or run.lock_hold_ms is not None:
        lines.append(f"  lock: wait={run.lock_wait_ms}ms hold={run.lock_hold_ms}ms")
    if run.leaked_lock:
        lines.append("  !! LOCK_LEAK: acquired but never released")

    for n in sorted(run.attempts):
        a = run.attempts[n]
        lines.append(
            f"  attempt {a.attempt}: difficulty={a.difficulty} styleReload={a.style_reload} "
            f"source={a.solver_source} captchaRound={a.captcha_round} submitMs={a.submit_ms}"
        )

    if run.other_flags:
        lines.append(f"  flags: {', '.join(run.other_flags)}")

    if run.dispatch_seen:
        lines.append(f"  result: ok={run.ok} resultType={run.result_type} totalMs={run.total_ms}")
        if run.no_completion:
            lines.append("  !! NO_COMPLETION: dispatched but no turbo_browser_done logged (process may have died)")
    else:
        lines.append("  result: (turbo flow never dispatched — likely lock timeout / early rejection)")

    if overlap_partners:
        lines.append(f"  !! OVERLAP with: {', '.join(overlap_partners)}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_path", type=Path, help="Path to debug-*.log (JSONL)")
    parser.add_argument("--since", type=int, default=None, help="Epoch ms lower bound (inclusive)")
    parser.add_argument("--until", type=int, default=None, help="Epoch ms upper bound (inclusive)")
    parser.add_argument("--run-id", default=None, help="Only show this runId")
    parser.add_argument("--anomalies-only", action="store_true", help="Only print runs with LOCK_LEAK/NO_COMPLETION/OVERLAP")
    args = parser.parse_args()

    if not args.log_path.exists():
        print(f"error: log file not found: {args.log_path}")
        return 1

    entries = load_entries(args.log_path, args.since, args.until)
    if not entries:
        print("No entries matched the given filters.")
        return 0

    timelines = build_timelines(entries)
    ordered = sorted(timelines.values(), key=lambda r: r.first_ts)
    overlaps = find_overlaps(ordered)

    acquired_total = sum(1 for r in ordered if r.lock_acquired_ts is not None)
    released_total = sum(1 for r in ordered if r.lock_released_ts is not None)
    leaked_total = sum(1 for r in ordered if r.leaked_lock)
    no_completion_total = sum(1 for r in ordered if r.no_completion)

    print(f"=== {len(ordered)} run(s) | lock acquired={acquired_total} released={released_total} leaked={leaked_total} | no_completion={no_completion_total} | overlapping={len(overlaps)} ===\n")

    for run in ordered:
        if args.run_id and run.run_id != args.run_id:
            continue
        partners = overlaps.get(run.run_id, [])
        is_anomaly = run.leaked_lock or run.no_completion or bool(partners)
        if args.anomalies_only and not is_anomaly:
            continue
        print(format_run(run, partners))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
