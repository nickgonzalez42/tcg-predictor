#!/usr/bin/env python3
"""The morning look at last night, sent as one email (2026-11-27).

The nightly emails when it FAILS. The quieter faults do not fail it: a step
skipped behind a non-fatal handler, data held back from the site, an article
shipped with fill-in text because the writer ran out of time, a crawler page
that lost its markup. Those were found by someone reading the log the next
morning, and on 2026-11-26 nobody did. This reads the night's state file and
log, runs the live site check (deploy/seo_check.py), asks the site for each
article the night wrote, and sends what it found to the problem-reports
topic: "all clear" or the list of things to look at.

    morning_check.py              # check last night, send the email
    morning_check.py --dry-run    # print the email, send nothing
    morning_check.py --no-live    # skip the live site check and article fetches
    morning_check.py --batch 2026-11-27

Runs as the last thing run_daily_refresh.sh does, once the night has
closed, so the email is there in the morning. A night that dies or hangs is
the watchdog's to report; a night that never starts sends no email at all,
and its absence is the sign. It reads files and fetches public pages; it
changes nothing.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE

SITE = "https://cardstock.guide"
STATE_DIR = os.path.join(BASE, "ml_data", "refresh_state")
LOG_DIR = os.path.expanduser("~/Library/Logs/tcg-predictor")
SEO_CHECK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "deploy", "seo_check.py")
SHOW = 3                 # lines quoted per kind of fault
PRERENDER_SLOW = 1200    # s; 211 to 763 over the nights of 2026-11-20..27
STALE_OVER = 200         # cards past the crawl cycle, as crawl_staleness alerts
DISK_LOW_GB = 5

# (pattern, what it means); each matching log line is quoted under it.
FAULTS = [
    (re.compile(r"FAILED|^✗ |rc=[1-9]\d* "), "A step failed"),
    (re.compile(r"non-fatal error"), "A step hit an error and was skipped"),
    (re.compile(r"not shipped tonight"), "Data was held back from the site"),
    (re.compile(r"CLI failed|CLI exit \d|bad JSON|no JSON in reply"),
     "The article writer failed, so that text is fill-in"),
    (re.compile(r"ran \d+ words against"), "A written section ran too long and was dropped"),
    (re.compile(r"read failed"), "An article could not read part of its data"),
    (re.compile(r"MATCH CHANGED|OVERRIDE UNRESOLVED"), "A card link needs a look"),
]
WROTE = re.compile(r"^wrote ((?:card-type|set-report|game-report|new-releases)-[a-z0-9-]+): (.+)$")
REPORTS = re.compile(r"^Wrote reports: ([a-z0-9-]+) .* and ([a-z0-9-]+)\s*$")
KEPT = re.compile(r"kept \d+ prose field\(s\) \((\d+)/(\d+) sections\)")
PRERENDER = re.compile(r"prerendered ([\d,]+) card pages .* in (\d+)s")
STALE = re.compile(r"^(graded|sales): ([\d,]+) card\(s\) over \d+ days")
INDEXNOW = re.compile(r"indexnow: answered (\d+)")


def clock(stamp):
    """'2026-11-27 04:21:31' -> '04:21'."""
    return stamp[11:16] if stamp else "?"


def read_state(text):
    """{"started", "finished" (site updated), "done", "stage"} from a night's
    state file; a missing key means it has not happened."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        stamp = " ".join(parts[-2:])
        if parts[0] == "started":
            out["started"] = stamp
        elif parts[0] == "finished":
            out["finished"] = stamp
            out["finished_ok"] = len(parts) > 1 and parts[1] == "ok"
        elif parts[0] == "done":
            out["done"] = stamp
        elif parts[0] == "stage":
            out["stage"] = parts[1]
    return out


def quote(lines):
    shown = [f"    {ln.strip()[:200]}" for ln in lines[:SHOW]]
    if len(lines) > SHOW:
        shown.append(f"    ({len(lines) - SHOW} more like it)")
    return shown


def review(batch, today, state_text, log_text):
    """What the night's own files say. -> (problems, fine, pages): two lists of
    text blocks, and the [(path, title)] the night wrote for the live look."""
    problems, fine, pages = [], [], []
    if batch != today:
        problems.append(f"No refresh ran last night. The newest night on file is {batch}.")
        return problems, fine, pages
    st = read_state(state_text or "")
    if st.get("done"):
        fine.append(f"The night finished at {clock(st['done'])}; the site was updated at "
                    f"{clock(st.get('finished'))}.")
    elif st.get("finished"):
        problems.append(f"The site was updated at {clock(st['finished'])}, but the night's last steps "
                        f"are still running (stage: {st.get('stage', '?')}).")
    else:
        problems.append(f"The site has NOT been updated yet. The night started at "
                        f"{clock(st.get('started'))} and is in stage: {st.get('stage', '?')}.")
    if st.get("finished") and not st.get("finished_ok"):
        problems.append("The night closed its site update as failed.")

    lines = (log_text or "").splitlines()
    for pattern, meaning in FAULTS:
        hit = [ln for ln in lines if pattern.search(ln)]
        if hit:
            problems.append("\n".join([f"{meaning} ({len(hit)}):"] + quote(hit)))
    short = [ln for ln in lines for m in [KEPT.search(ln)] if m and int(m.group(1)) < int(m.group(2))]
    if short:
        problems.append("\n".join([f"An article kept only part of its written text ({len(short)}):"]
                                  + quote(short)))
    for ln in lines:
        m = STALE.match(ln.strip())
        if m and int(m.group(2).replace(",", "")) > STALE_OVER:
            problems.append(f"The {m.group(1)} crawl is behind: {ln.strip()}")
        m = INDEXNOW.search(ln)
        if m and m.group(1) not in ("200", "202"):
            problems.append(f"The search-engine ping was refused: {ln.strip()}")
    pre = [m for m in map(PRERENDER.search, lines) if m]
    if st.get("finished") and not pre:
        problems.append("The crawler pages were not rebuilt (no prerender line in the log).")
    elif pre and int(pre[-1].group(2)) > PRERENDER_SLOW:
        problems.append(f"The crawler pages took {pre[-1].group(2)} s to rebuild (usually under 800).")
    elif pre:
        fine.append(f"Crawler pages rebuilt: {pre[-1].group(1)} cards in {pre[-1].group(2)} s.")

    for ln in lines:
        m = WROTE.match(ln.strip())
        if m:
            pages.append((f"/articles/{m.group(1)}", m.group(2).strip("'\"")))
        m = REPORTS.match(ln.strip())
        if m:
            pages += [(f"/reports/{m.group(1)}", "market report"), (f"/reports/{m.group(2)}", "model report")]
    pages = list(dict(pages).items())            # a resumed night logs a page twice
    return problems, fine, pages


def live(pages):
    """The site as a visitor and a crawler get it. -> (problems, fine)."""
    import requests
    problems, fine = [], []
    try:
        out = subprocess.run([sys.executable, SEO_CHECK], capture_output=True, text=True, timeout=900)
        passed = [ln for ln in out.stdout.splitlines() if ln.startswith("PASS")]
        failed = [ln for ln in out.stdout.splitlines() if ln.startswith("FAIL")]
        if failed or not passed:
            crashed = "" if passed or failed else " (it could not run: " + out.stderr.strip()[-200:] + ")"
            problems.append("\n".join([f"Live site check: {len(failed)} of {len(passed) + len(failed)} "
                                       f"failing{crashed}:"] + quote([ln[6:] for ln in failed])))
        else:
            fine.append(f"Live site check: {len(passed)} of {len(passed)} pass.")
    except (subprocess.TimeoutExpired, OSError) as e:
        problems.append(f"Live site check could not run ({type(e).__name__}).")
    missing = []
    for path, title in pages:
        try:
            code = requests.get(SITE + path, timeout=30, headers={"User-Agent": "cardstock-morning-check"}).status_code
        except requests.RequestException as e:
            code = type(e).__name__
        if code != 200:
            missing.append(f"{path} answered {code}")
    if missing:
        problems.append("\n".join([f"Written last night but not on the site ({len(missing)}):"] + quote(missing)))
    elif pages:
        fine.append("\n".join([f"Written last night and live ({len(pages)}):"]
                              + [f"    {title[:110]}" for _path, title in pages]))
    return problems, fine


def disk():
    free = shutil.disk_usage(os.path.expanduser("~")).free / 1e9
    if free < DISK_LOW_GB:
        return [f"The Mac has {free:.1f} GB free; the nightly needs room to work."], []
    return [], [f"Mac disk: {free:.1f} GB free."]


def compose(batch_day, problems, fine, log_path):
    """(subject, body). batch_day is a date."""
    day = batch_day.strftime("%b %-d")
    verdict = ("all clear" if not problems else
               f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to look at")
    body = []
    if problems:
        body += ["TO LOOK AT", ""] + [f"- {p}" for p in problems] + [""]
    body += ["FINE", ""] + [f"- {f}" for f in fine] + ["", f"Log: {log_path}"]
    return f"CardStock morning check, {day}: {verdict}", "\n".join(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the email, send nothing")
    ap.add_argument("--no-live", action="store_true", help="skip the live site check and article fetches")
    ap.add_argument("--batch", help="the night to check, by its morning date (default: the newest on file)")
    args = ap.parse_args()
    today = datetime.now().date()
    try:
        states = sorted(glob.glob(os.path.join(STATE_DIR, "*.state")))
        batch = args.batch or (os.path.basename(states[-1])[:-len(".state")] if states else "none")
        log_path = os.path.join(LOG_DIR, f"refresh-{batch}.log")

        def text(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return ""
        problems, fine, pages = review(batch, args.batch or today.isoformat(),
                                       text(os.path.join(STATE_DIR, f"{batch}.state")), text(log_path))
        if not args.no_live:
            p, f = live(pages)
            problems, fine = problems + p, fine + f
        p, f = disk()
        problems, fine = problems + p, fine + f
        subject, body = compose(datetime.strptime(batch, "%Y-%m-%d").date() if batch != "none" else today,
                                problems, fine, log_path)
    except Exception as e:                                       # noqa: BLE001
        subject = f"CardStock morning check, {today.strftime('%b %-d')}: the check itself broke"
        body = f"{type(e).__name__}: {e}\n\nRun pipeline/morning_check.py --dry-run to see it."
    print(subject + "\n\n" + body)
    if not args.dry_run:
        from crawl_staleness import send
        send(subject, body)


if __name__ == "__main__":
    main()
