"""
Weekly market report generator — runs inside the daily refresh but only
writes on Fridays (or with --force). Builds a "Weekly Market Report" post
from the last ~7 days of daily price snapshots (graded_price_history,
ungraded tier), plus a model corner from the live forecasts, and stores it
in a `reports` table in predictions.db — so reports ship to the server with
the normal data push and the API serves them read-only at /api/market-reports.

Run:  .venv/bin/python market_report.py            # no-op unless Friday
      .venv/bin/python market_report.py --force    # write this week's report now
"""

import argparse
import html
import os
import sqlite3
import statistics
from datetime import date, datetime, timedelta

from _paths import DATA_DIR as BASE
from games import GAMES

API_DATA = os.path.normpath(os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards"))
PC_DB = os.path.join(API_DATA, "pricecharting.db")
PRED_DB = os.path.join(API_DATA, "predictions.db")

MIN_BASE = 5.0        # penny floor: % moves under this base price are noise
TOP_N = 3             # per-game gainers/losers
OVERALL_N = 5         # cross-game movers table


def esc(s):
    return html.escape(str(s), quote=True)


def card_link(game, pid, name):
    return f'<a href="/catalog/{game}/{pid}">{esc(name)}</a>'


def week_window(pc):
    """Latest snapshot date, and the closest snapshot ~7 days before it."""
    dates = [r[0] for r in pc.execute(
        "SELECT DISTINCT date FROM graded_price_history WHERE grade='ungraded' ORDER BY date")]
    if len(dates) < 2:
        return None, None
    latest = dates[-1]
    target = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    baseline = max((d for d in dates if d <= target), default=dates[0])
    return baseline, latest


def visible_cards(game):
    """id -> name for cards the site actually shows (art + real price)."""
    con = sqlite3.connect(os.path.join(BASE, GAMES[game]["db"]))
    rows = con.execute(
        "SELECT product_id, name FROM cards WHERE image_path IS NOT NULL AND image_path != '' "
        "AND near_mint_price IS NOT NULL AND name IS NOT NULL").fetchall()
    con.close()
    return dict(rows)


def game_moves(pc, game, baseline, latest, names):
    """[(pid, name, old, new, pct)] for visible cards priced on both dates."""
    rows = pc.execute(
        "SELECT a.product_id, a.price, b.price FROM graded_price_history a "
        "JOIN graded_price_history b ON b.game=a.game AND b.product_id=a.product_id "
        "AND b.grade=a.grade AND b.date=? "
        "WHERE a.game=? AND a.grade='ungraded' AND a.date=? AND a.price>=?",
        (latest, game, baseline, MIN_BASE)).fetchall()
    moves = []
    for pid, old, new in rows:
        if pid in names and old > 0:
            moves.append((pid, names[pid], old, new, (new / old - 1) * 100))
    return moves


def forecast_corner(pred, games_live):
    """Top model 1M forecast gainers across live games (base >= $10)."""
    rows = pred.execute(
        "SELECT game, product_id, base_price, forecast_price FROM forecasts "
        "WHERE target='ungraded' AND horizon='1m' AND base_price>=10 "
        "ORDER BY forecast_price/base_price DESC LIMIT 30").fetchall()
    picks = []
    for game, pid, base, fcst in rows:
        if game in games_live and pid in games_live[game]:
            picks.append((game, pid, games_live[game][pid], base, fcst))
        if len(picks) == OVERALL_N:
            break
    return picks


def money(v):
    return f"${v:,.2f}"


def pct(v):
    return f"{v:+.1f}%"


def build_report(force=False):
    today = date.today()
    if today.weekday() != 4 and not force:   # 4 = Friday
        print("Not Friday — skipping (use --force to write anyway).")
        return

    pc = sqlite3.connect(PC_DB)
    pred = sqlite3.connect(PRED_DB)
    baseline, latest = week_window(pc)
    if not baseline or baseline == latest:
        print("Not enough snapshot history for a weekly window — skipping.")
        return

    games_live = {}
    per_game = {}
    all_moves = []
    for game in GAMES:
        names = visible_cards(game)
        if not names:
            continue
        moves = game_moves(pc, game, baseline, latest, names)
        if not moves:
            continue
        games_live[game] = names
        per_game[game] = moves
        all_moves += [(game, *m) for m in moves]

    if not all_moves:
        print("No movement data — skipping.")
        return

    n = len(all_moves)
    gains = [m[5] for m in all_moves if m[5] > 0.5]
    losses = [m[5] for m in all_moves if m[5] < -0.5]
    ups, downs = len(gains), len(losses)
    avg_gain = statistics.mean(gains) if gains else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    breadth = "more gainers than losers" if ups > downs else \
              "more losers than gainers" if downs > ups else "an even split"

    title = f"Weekly Market Report — {today.strftime('%B %-d, %Y')}"
    slug = f"weekly-market-report-{today.isoformat()}"
    summary = (f"The card market showed {breadth} this week: of {n:,} cards priced "
               f"at both ends of the window, {ups:,} rose an average of {avg_gain:.1f}% "
               f"and {downs:,} fell an average of {abs(avg_loss):.1f}%.")

    body = [f"<p class='report-lede'>{esc(summary)} Price window: {baseline} to {latest}, "
            f"ungraded market prices.</p>"]

    top = sorted(all_moves, key=lambda m: -m[5])[:OVERALL_N]
    bottom = sorted(all_moves, key=lambda m: m[5])[:OVERALL_N]
    for label, rows in (("Biggest gainers", top), ("Biggest losers", bottom)):
        body.append(f"<h2>{label} this week</h2><table class='report-table'>"
                    "<thead><tr><th>Card</th><th>Then</th><th>Now</th><th>Move</th></tr></thead><tbody>")
        for game, pid, name, old, new, p in rows:
            body.append(f"<tr><td>{card_link(game, pid, name)} "
                        f"<span class='report-game'>{esc(GAMES[game]['label'])}</span></td>"
                        f"<td>{money(old)}</td><td>{money(new)}</td><td>{pct(p)}</td></tr>")
        body.append("</tbody></table>")

    for game, moves in per_game.items():
        ggains = [m[4] for m in moves if m[4] > 0.5]
        glosses = [m[4] for m in moves if m[4] < -0.5]
        gups, gdowns = len(ggains), len(glosses)
        gavg_gain = statistics.mean(ggains) if ggains else 0.0
        gavg_loss = statistics.mean(glosses) if glosses else 0.0
        body.append(f"<h2>{esc(GAMES[game]['label'])}</h2>"
                    f"<p>{gups:,} of {len(moves):,} tracked cards rose this week "
                    f"(up an average of {gavg_gain:.1f}%); {gdowns:,} fell "
                    f"(down an average of {abs(gavg_loss):.1f}%).</p>")
        gtop = sorted(moves, key=lambda m: -m[4])[:TOP_N]
        gbot = sorted(moves, key=lambda m: m[4])[:TOP_N]
        body.append("<table class='report-table'>"
                    "<thead><tr><th>Card</th><th>Then</th><th>Now</th><th>Move</th></tr></thead><tbody>")
        for pid, name, old, new, p in gtop + gbot:
            body.append(f"<tr><td>{card_link(game, pid, name)}</td>"
                        f"<td>{money(old)}</td><td>{money(new)}</td><td>{pct(p)}</td></tr>")
        body.append("</tbody></table>")

    picks = forecast_corner(pred, games_live)
    if picks:
        body.append("<h2>Model corner</h2>"
                    "<p>The cards our 1-month model is most optimistic about right now:</p>"
                    "<table class='report-table'>"
                    "<thead><tr><th>Card</th><th>Current</th><th>1M forecast</th><th>Implied</th></tr></thead><tbody>")
        for game, pid, name, base, fcst in picks:
            body.append(f"<tr><td>{card_link(game, pid, name)} "
                        f"<span class='report-game'>{esc(GAMES[game]['label'])}</span></td>"
                        f"<td>{money(base)}</td><td>{money(fcst)}</td>"
                        f"<td>{pct((fcst / base - 1) * 100)}</td></tr>")
        body.append("</tbody></table>"
                    "<p class='report-note'>Forecasts are model estimates, not financial advice; "
                    "see the About page for how they work and how they're graded.</p>")

    pred.execute("""CREATE TABLE IF NOT EXISTS reports (
        slug TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        published_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        body_html TEXT NOT NULL)""")
    pred.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?)",
                 (slug, title, today.isoformat(), summary, "".join(body)))
    pred.commit()
    pc.close()
    pred.close()
    print(f"Wrote report: {slug} ({n:,} cards in window {baseline} → {latest})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Weekly market report generator")
    ap.add_argument("--force", action="store_true", help="write even if today isn't Friday")
    build_report(force=ap.parse_args().force)
