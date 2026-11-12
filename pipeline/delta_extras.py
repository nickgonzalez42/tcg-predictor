#!/usr/bin/env python3
"""The nightly delta's second half: daily prices, graded crawl rows, sales counts.

build_unified_history.py stages the night's changes to price_history_unified
(and the match rows) in unified_delta.db, and push_data.sh applies that on the
server instead of shipping the multi-GB pricecharting.db. Nothing else in the
database rode along, so between two full pushes (Sundays) the server's other
tables stood still: on Thursday 2026-11-12 its newest daily Near Mint price
was Monday's, card charts were missing the days since, a printing's "now"
price was three days old and "copies sold each week" four.

These tables are plain rowid tables and every writer uses INSERT OR REPLACE,
so a new or re-written row always lands ABOVE the highest rowid the table had
before. The server's copy is a byte copy of ours as of the last full push
(same rowids), so the rows above the last shipped rowid are exactly what it is
missing, whatever their dates. Deletions do not ride (hand review tools only);
the next full push restores parity, as it does for everything else.

  stage      rows above the shipped marks -> ml_data/delta_extras.db
  apply-sql  the SQL the server runs against that file [its path there]
  mark       the staged file was applied: move the marks up, drop the file
  capture    before a full push: note the marks the pushed file carries
  mark-full  the full push landed: adopt the captured marks
  set        hand repair: set T=ROWID [T=ROWID ...]
  status     the marks and how far the local tables are past them

push_data.sh drives all of it; a failure here never fails the push (the marks
stay put, so the next night's file carries the same rows again).
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from _paths import DATA_DIR as BASE  # data lives in the sibling one-piece/ dir

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
EXTRAS_DB = os.path.join(BASE, "ml_data", "delta_extras.db")
STATE = os.path.join(BASE, "ml_data", "delta_extras_state.json")
PENDING = os.path.join(BASE, "ml_data", "delta_extras_pending.json")

# Every table of pricecharting.db the site reads that the unified delta does
# not carry. Sales ride in every condition: the card page's "copies sold"
# line adds the other four to the Near Mint count.
TABLES = ("tcg_nm_history", "graded_price_history", "tcg_sales_scan", "tcg_sales_history")
READ_STEP = 200_000    # rowids per read: short shared locks next to a crawl
# The server's database runs a rollback journal, where a writer shuts readers
# out from the moment its page cache overflows until it commits. Each new row
# dirties about one index page, so 10,000-row transactions on the default
# 8 MB cache held that lock nearly non-stop and card pages took 10-17 s
# (measured 2026-11-12). Small transactions on a cache that holds all of
# their pages only lock readers out for the commit's write.
APPLY_STEP = 5_000     # rows per transaction on the server
APPLY_CACHE_KB = 131_072
MAX_ROWS = 8_000_000   # further behind than this is a full push's job


def source():
    return sqlite3.connect(f"file:{os.path.normpath(PC_DB)}?mode=ro", uri=True, timeout=120)


def high_marks(conn):
    return {t: conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {t}").fetchone()[0]
            for t in TABLES}


def read_state():
    with open(STATE) as f:
        return json.load(f)["marks"]


def write_state(marks, via):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"marks": marks, "via": via,
                   "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f, indent=1)
    os.replace(tmp, STATE)


def stage():
    if os.path.exists(EXTRAS_DB):
        os.remove(EXTRAS_DB)
    if not os.path.exists(STATE):
        print("delta extras: no marks yet (the next full push sets them)")
        return 3
    marks = read_state()
    src = source()
    upto = high_marks(src)
    for t in TABLES:
        if t not in marks or upto[t] < marks[t]:
            print(f"delta extras: {t} has no usable mark (table rebuilt?); waiting for a full push")
            return 4
    todo = sum(upto[t] - marks[t] for t in TABLES)
    if todo > MAX_ROWS:
        print(f"delta extras: {todo:,} rowids behind, over the {MAX_ROWS:,} cap; waiting for a full push")
        return 4
    out = sqlite3.connect(EXTRAS_DB)
    out.execute("CREATE TABLE meta (tbl TEXT PRIMARY KEY, lo INTEGER, upto INTEGER, n INTEGER)")
    for t in TABLES:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
        out.execute(f"CREATE TABLE x_{t} ({', '.join(cols)})")
        ins = f"INSERT INTO x_{t} VALUES ({','.join('?' * len(cols))})"
        sel = f"SELECT {', '.join(cols)} FROM {t} WHERE rowid > ? AND rowid <= ? ORDER BY rowid"
        n = 0
        for a in range(marks[t], upto[t], READ_STEP):
            rows = src.execute(sel, (a, min(a + READ_STEP, upto[t]))).fetchall()
            out.executemany(ins, rows)
            n += len(rows)
        out.execute("INSERT INTO meta VALUES (?,?,?,?)", (t, marks[t], upto[t], n))
        print(f"  {t}: {n:,} row(s) (rowid {marks[t]:,} -> {upto[t]:,})")
    out.commit()
    out.close()
    src.close()
    print(f"delta extras staged -> {os.path.normpath(EXTRAS_DB)} "
          f"({os.path.getsize(EXTRAS_DB) / 1e6:.1f} MB)")
    return 0


def apply_sql(remote="/tmp/delta_extras.db"):
    d = sqlite3.connect(f"file:{EXTRAS_DB}?mode=ro", uri=True)
    print(f"ATTACH '{remote}' AS d;")
    print(".timeout 60000")
    print(f"PRAGMA cache_size = -{APPLY_CACHE_KB};")
    for t, n in d.execute("SELECT tbl, n FROM meta ORDER BY rowid"):
        cols = ", ".join(r[1] for r in d.execute(f"PRAGMA table_info(x_{t})"))
        for a in range(0, n, APPLY_STEP):
            print(f"BEGIN IMMEDIATE; INSERT OR REPLACE INTO {t} ({cols}) "
                  f"SELECT {cols} FROM d.x_{t} WHERE rowid > {a} AND rowid <= {a + APPLY_STEP}; COMMIT;")
    print("SELECT 'extras applied';")
    return 0


def mark():
    d = sqlite3.connect(f"file:{EXTRAS_DB}?mode=ro", uri=True)
    marks = read_state()
    marks.update({t: upto for t, upto in d.execute("SELECT tbl, upto FROM meta")})
    d.close()
    write_state(marks, "delta")
    os.remove(EXTRAS_DB)  # shipped; ml_data rides other syncs, keep it lean
    print(f"delta extras: marks -> {marks}")
    return 0


def capture():
    if os.path.exists(PENDING):
        os.remove(PENDING)
    src = source()
    with open(PENDING, "w") as f:
        json.dump(high_marks(src), f)
    return 0


def mark_full():
    if not os.path.exists(PENDING):
        print("delta extras: no capture to adopt")
        return 1
    with open(PENDING) as f:
        marks = json.load(f)
    write_state(marks, "full")
    os.remove(PENDING)
    print(f"delta extras: marks (full push) -> {marks}")
    return 0


def set_marks(pairs):
    marks = read_state() if os.path.exists(STATE) else {}
    for p in pairs:
        t, _, v = p.partition("=")
        if t not in TABLES:
            sys.exit(f"unknown table {t!r}")
        marks[t] = int(v)
    write_state(marks, "hand")
    print(f"delta extras: marks -> {marks}")
    return 0


def status():
    if not os.path.exists(STATE):
        print("no marks yet")
        return 0
    with open(STATE) as f:
        st = json.load(f)
    now = high_marks(source())
    print(f"marks set {st['updated']} via {st['via']}")
    for t in TABLES:
        print(f"  {t}: shipped to {st['marks'].get(t, 0):,}, local at {now[t]:,} "
              f"(+{now[t] - st['marks'].get(t, 0):,})")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    run = {"stage": stage, "apply-sql": apply_sql, "mark": mark, "capture": capture,
           "mark-full": mark_full, "status": status}.get(cmd)
    if cmd == "set":
        sys.exit(set_marks(sys.argv[2:]))
    if cmd == "apply-sql":
        sys.exit(apply_sql(*sys.argv[2:3]))
    if not run:
        sys.exit(__doc__)
    sys.exit(run())
