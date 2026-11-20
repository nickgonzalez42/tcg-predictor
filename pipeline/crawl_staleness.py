#!/usr/bin/env python3
"""Say so when the daytime crawls fall behind (2026-11-20).

The graded and sales crawls promise every card a fresh fetch within 28 days.
Nothing watched that promise: on 2026-10-29 the graded crawl was found three
cycles behind (120,808 cards overdue) only because someone looked. This
counts the cards past 32 days (the cycle plus four days of slack) and sends
one message to the problem-reports topic when either crawl has more than
ALERT_OVER of them. Run as the nightly's last tail step; it never fails it.

    crawl_staleness.py            # print the counts, alert when over
    crawl_staleness.py --dry-run  # print only
"""
import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import PC_DB

STALE_DAYS = 32
ALERT_OVER = 200          # cards; a handful of stragglers is not news
AWS = "/usr/local/bin/aws"
ACCOUNT = "522029196375"
TOPIC = f"arn:aws:sns:us-east-1:{ACCOUNT}:cardstock-problem-reports"


def counts(pc, now=None):
    """{"graded": (stale, oldest), "sales": (stale, oldest)}: cards whose last
    fetch is over STALE_DAYS old. Graded counts linked cards only and leaves
    out pages known dead; a card never fetched is not counted (a new link is
    fetched first on the next run, and has no date to be late against)."""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
    graded = pc.execute(
        "SELECT COUNT(*), MIN(c.crawled_at) FROM graded_crawled c "
        "JOIN pricecharting p ON p.game=c.game AND p.product_id=c.product_id "
        "WHERE p.pc_id IS NOT NULL AND c.crawled_at < ? AND NOT EXISTS ("
        "  SELECT 1 FROM graded_dead d WHERE d.game=c.game AND d.product_id=c.product_id)",
        (cut,)).fetchone()
    # fetched_at is 'YYYY-MM-DD HH:MM:SS' on some rows and ISO on others; the
    # first ten characters compare the same either way.
    sales = pc.execute(
        "SELECT COUNT(*), MIN(fetched_at) FROM tcg_sales_scan "
        "WHERE substr(fetched_at, 1, 10) < ?", (cut[:10],)).fetchone()
    return {"graded": (graded[0], graded[1]), "sales": (sales[0], sales[1])}


def message(found):
    """(subject, body) when a crawl is over the line, else None."""
    late = {k: v for k, v in found.items() if v[0] > ALERT_OVER}
    if not late:
        return None
    lines = [f"{k} crawl: {n:,} card(s) not fetched in {STALE_DAYS} days "
             f"(oldest {str(oldest)[:10]})" for k, (n, oldest) in late.items()]
    lines.append("Check the agent: launchctl list | grep tcg-predictor, and the "
                 "log in ~/Library/Logs/tcg-predictor/.")
    return "cardstock crawl is behind", "\n".join(lines)


def send(subject, body):
    env = dict(os.environ, AWS_PROFILE="default", AWS_DEFAULT_REGION="us-east-1")
    acct = subprocess.run([AWS, "sts", "get-caller-identity", "--query", "Account",
                           "--output", "text"], env=env, capture_output=True, text=True,
                          timeout=60).stdout.strip()
    if acct != ACCOUNT:                      # same account pin as the refresh
        print(f"(no message: aws resolves to account '{acct or 'none'}')")
        return
    subprocess.run([AWS, "sns", "publish", "--region", "us-east-1", "--topic-arn", TOPIC,
                    "--subject", subject, "--message", body], env=env,
                   capture_output=True, timeout=60)
    print("message sent")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        pc = sqlite3.connect(f"file:{os.path.normpath(PC_DB)}?mode=ro", uri=True, timeout=120)
        found = counts(pc)
        pc.close()
        for k, (n, oldest) in found.items():
            print(f"{k}: {n:,} card(s) over {STALE_DAYS} days (oldest {str(oldest)[:10]})")
        msg = message(found)
        if msg and not args.dry_run:
            send(*msg)
        elif msg:
            print(f"dry run, would send: {msg[0]}\n{msg[1]}")
    except Exception as e:                                       # noqa: BLE001
        print(f"non-fatal error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
