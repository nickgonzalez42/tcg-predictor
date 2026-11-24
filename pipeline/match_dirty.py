"""Match rows changed by hand, so the nightly ships them (2026-11-24).

The nightly's delta to the server carries a match row (the pricecharting
table: which PriceCharting page a card is linked to, and its current graded
prices) only when build_unified_history sees the card's graded prices change.
A link fixed by hand changes the page and blanks the prices, and when the new
page has no graded prices nothing "changes" again: the server kept the old
link and the old page's prices until Sunday's full push (2026-11-20: 2,551
cards moved off foil pages had to be mirrored on the server by hand, and 262
more on 2026-11-24 would have waited five days).

Every tool that re-points or unlinks a match row calls mark() in the same
transaction. build_unified_history adds the marked rows to the night's delta.
A mark is kept MARK_DAYS, so it rides every delta until a Sunday full push
has certainly carried it; shipping a row twice is harmless (the server does
INSERT OR REPLACE with the whole row).

Not covered: a match row that is DELETED (match_review's blocklist). Its card
leaves the catalog the same night, so the leftover row on the server is
never read; it goes with Sunday's full push as before.
"""
from datetime import datetime, timedelta, timezone

MARK_DAYS = 8


def _ensure(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS match_dirty (game TEXT NOT NULL, product_id INTEGER NOT NULL, "
        "marked_at TEXT NOT NULL, PRIMARY KEY (game, product_id)) WITHOUT ROWID")


def mark(conn, game, product_id, now=None):
    """Call beside the UPDATE, on the same connection; the caller commits."""
    _ensure(conn)
    now = now or datetime.now(timezone.utc)
    conn.execute("INSERT OR REPLACE INTO match_dirty VALUES (?,?,?)",
                 (game, int(product_id), now.isoformat(timespec="seconds")))


def pending(conn, now=None):
    """{(game, product_id)} still to ride tonight's delta. Marks older than
    MARK_DAYS are dropped first; the caller commits."""
    _ensure(conn)
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=MARK_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM match_dirty WHERE marked_at < ?", (cut,))
    return {(g, p) for g, p in conn.execute("SELECT game, product_id FROM match_dirty")}
