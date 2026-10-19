#!/usr/bin/env python3
"""
Run ledger for the nightly refresh — when each night started, hit its phase
boundaries, and finished, and how that landed against the finish-by target
(TCG_REFRESH_DEADLINE, default 06:00 local). Added 2026-10-18 so the
schedule can be VERIFIED rather than estimated: the user wants each night's
end time recorded.

Ledger: one-piece/ml_data/refresh_runs.csv — one row per run keyed by the
run's UTC day (the same label the log file carries), local timestamps for
reading. It lives in ml_data so the nightly S3 CSV backup carries it.

Events, in the order run_daily_refresh.sh reaches them:
  started      the script began
  phase_a      crawls/prices/build done (weekly_refresh --to art-comps)
  model        local model block done, predictions pushed
  deploy       s3 art + link drain + database push done
  finished     everything incl. backups; --status ok|failed

Run:
  refresh_ledger.py record --event started --kind csv       # from the .sh
  refresh_ledger.py record --event finished --status ok
  refresh_ledger.py backfill          # seed from existing refresh-*.log files
  refresh_ledger.py report [--last 12]
"""

import argparse
import csv
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

LEDGER = os.path.join(BASE, "ml_data", "refresh_runs.csv")
LOG_GLOB = os.path.expanduser("~/Library/Logs/tcg-predictor/refresh-*.log")
COLS = ["run", "kind", "started", "phase_a", "model", "deploy", "finished",
        "status", "hours", "vs_deadline_min", "notes"]
TS = "%Y-%m-%d %H:%M"


def deadline_str():
    return os.environ.get("TCG_REFRESH_DEADLINE", "").strip() or "06:00"


def run_label(now_utc=None):
    """The run's UTC day — identical to the refresh-<day>.log name."""
    return (now_utc or datetime.now(timezone.utc)).date().isoformat()


def load():
    rows = {}
    if os.path.exists(LEDGER):
        with open(LEDGER, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["run"]] = {c: r.get(c, "") for c in COLS}
    return rows


def save(rows):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])
    os.replace(tmp, LEDGER)


def derive(row):
    """Fill hours and vs_deadline_min from started/finished."""
    try:
        s = datetime.strptime(row["started"], TS)
        e = datetime.strptime(row["finished"], TS)
    except (ValueError, KeyError):
        return row
    row["hours"] = f"{(e - s).total_seconds() / 3600:.1f}"
    hh, mm = (int(x) for x in deadline_str().split(":"))
    target = s.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= s:
        target += timedelta(days=1)
    row["vs_deadline_min"] = str(int(round((e - target).total_seconds() / 60)))
    return row


def cmd_record(a):
    rows = load()
    run = a.run or run_label()
    row = rows.get(run) or {c: "" for c in COLS}
    row["run"] = run
    if a.kind:
        row["kind"] = a.kind
    row[a.event] = datetime.now().strftime(TS)
    if a.status:
        row["status"] = a.status
    if a.notes:
        row["notes"] = (row["notes"] + "; " if row["notes"] else "") + a.notes
    rows[run] = derive(row)
    save(rows)
    print(f"ledger: {run} {a.event} @ {row[a.event]}"
          + (f" ({a.status})" if a.status else ""))


def parse_log(path):
    """(run, kind, started, model_started, finished, status) from one log."""
    run = re.search(r"refresh-(\d{4}-\d{2}-\d{2})\.log$", path).group(1)
    first = started = model = finished = status = kind = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if first is None:
                first = line
                # Headers over time: "MONDAY full refresh" (old Monday
                # rebuild), "WEEKLY full refresh" (crawl night), "daily
                # refresh" (CSV night). Anything else stays unlabeled.
                kind = ("crawl" if ("WEEKLY" in line or "MONDAY" in line)
                        else "csv" if "daily" in line else "")
                m = re.search(r"— (\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2} ===", line)
                started = m.group(1) if m else None
            elif line.startswith("=== LOCAL model block"):
                m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2}", line)
                model = m.group(1) if m else model
            elif line.startswith("=== refresh complete") or line.startswith("=== FAILED"):
                m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2}", line)
                if m:
                    finished = m.group(1)
                    status = "ok" if "complete" in line else \
                        "failed " + (re.search(r"FAILED \((Phase [A-C][^,)]*)", line) or [None, "?"])[1]
    return run, kind, started, model, finished, status


def cmd_backfill(a):
    rows = load()
    n = 0
    for path in sorted(glob.glob(LOG_GLOB)):
        run, kind, started, model, finished, status = parse_log(path)
        if not started:
            continue
        row = rows.get(run) or {c: "" for c in COLS}
        if row.get("started") and not a.force:
            continue
        row.update({"run": run, "kind": kind or row.get("kind", ""), "started": started,
                    "finished": finished or "", "status": status or ("running" if not finished else ""),
                    "notes": "backfilled from log"})
        # The old logs only timestamp the model block's start; keep it as a
        # phase marker so the pre-ledger nights still show where the time went.
        if model:
            row["phase_a"] = model
        rows[run] = derive(row)
        n += 1
    save(rows)
    print(f"ledger: {n} run(s) seeded from logs -> {LEDGER}")


def cmd_report(a):
    rows = load()
    keys = sorted(rows)[-a.last:]
    dl = deadline_str()
    print(f"{'run':10s} {'kind':5s} {'started':16s} {'finished':16s} {'hours':>5s} "
          f"{'vs ' + dl:>9s}  status")
    for k in keys:
        r = rows[k]
        vs = r.get("vs_deadline_min", "")
        vs_s = ("+" if vs and int(vs) > 0 else "") + (f"{int(vs) // 60}h{abs(int(vs)) % 60:02d}" if vs else "")
        print(f"{r['run']:10s} {r['kind']:5s} {r['started']:16s} {r['finished'] or '(running)':16s} "
              f"{r['hours']:>5s} {vs_s:>9s}  {r['status']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    p.add_argument("--event", required=True, choices=["started", "phase_a", "model", "deploy", "finished"])
    p.add_argument("--kind", choices=["csv", "crawl"])
    p.add_argument("--status", choices=["ok", "failed"])
    p.add_argument("--run", help="UTC day label (default: today UTC)")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("backfill")
    p.add_argument("--force", action="store_true", help="overwrite rows that already have a start")
    p.set_defaults(fn=cmd_backfill)
    p = sub.add_parser("report")
    p.add_argument("--last", type=int, default=12)
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
