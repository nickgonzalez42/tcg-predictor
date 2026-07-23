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
from math import exp

from _paths import DATA_DIR as BASE
from games import GAMES

API_DATA = os.path.normpath(os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards"))
PC_DB = os.path.join(API_DATA, "pricecharting.db")
PRED_DB = os.path.join(API_DATA, "predictions.db")

MIN_BASE = 5.0        # penny floor: % moves under this base price are noise
TOP_N = 3             # per-game gainers/losers
OVERALL_N = 5         # cross-game movers table
# Listing-repair guard: PriceCharting occasionally re-bases a listing (flat at
# one value, an overnight 5-10x step, flat at the new value — e.g. a promo
# whose cheap-variant pollution got corrected). That is a data regime change,
# not a market move, so week-over-week ratios beyond this bound (either
# direction) stay out of the report's stats, tables, and graphs entirely.
MAX_WEEK_RATIO = 5.0


def esc(s):
    return html.escape(str(s), quote=True)


def card_link(game, pid, name):
    return f'<a href="/catalog/{game}/{pid}">{esc(name)}</a>'


def week_window(pc):
    """(baseline, latest, every snapshot date between them) for the ~7-day window."""
    dates = [r[0] for r in pc.execute(
        "SELECT DISTINCT date FROM graded_price_history WHERE grade='ungraded' ORDER BY date")]
    if len(dates) < 2:
        return None, None, []
    latest = dates[-1]
    target = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    baseline = max((d for d in dates if d <= target), default=dates[0])
    return baseline, latest, [d for d in dates if baseline <= d <= latest]


def visible_cards(game):
    """id -> name for priced, named cards. Art is deliberately NOT required —
    it once was, and that silently dropped whole games (Magic's art backfill
    is still running) from the report's market stats."""
    con = sqlite3.connect(os.path.join(BASE, GAMES[game]["db"]))
    rows = con.execute(
        "SELECT product_id, name FROM cards "
        "WHERE near_mint_price IS NOT NULL AND name IS NOT NULL").fetchall()
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
        if pid in names and old > 0 and 1 / MAX_WEEK_RATIO <= new / old <= MAX_WEEK_RATIO:
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


# Site-served horizons and their fixed display lengths (the pipeline also
# grades 1w internally as a feedback signal, but 1-week forecasts are not a
# product the site shows, so they stay out of the public report card).
REPORT_HORIZONS = (("1m", "1 month", 28), ("6m", "6 months", 182), ("12m", "12 months", 364))
MIN_GRADED = 30   # a horizon/game needs this many graded calls to be worth reporting

ACCURACY_SELECT = (
    "SELECT {cols} COUNT(*), AVG(ABS(ret - realized_ret)), AVG(ret - realized_ret), "
    "AVG(CASE WHEN realized_price BETWEEN low AND high THEN 1.0 ELSE 0.0 END) "
    "FROM forecast_archive WHERE realized_ret IS NOT NULL AND ")


def live_accuracy(pred, horizon, days, by_game=False):
    """Graded LIVE forecasts of a horizon whose window has genuinely elapsed
    since issue (scored_at + horizon <= today). Stale-anchored archive rows
    grade instantly against old history — real cohorts take wall-clock time,
    so without this gate the report would claim matured 6-month calls months
    before the first one could exist."""
    where = ("substr(model_version,1,2) != '__' AND horizon = ? "
             "AND date(substr(scored_at,1,10)) <= date('now', ?)")
    args = (horizon, f"-{days} days")
    if by_game:
        return pred.execute(ACCURACY_SELECT.format(cols="game,") + where +
                            " GROUP BY game HAVING COUNT(*) >= " + str(MIN_GRADED),
                            args).fetchall()
    r = pred.execute(ACCURACY_SELECT.format(cols="") + where, args).fetchone()
    return r if r[0] and r[0] >= MIN_GRADED else None


def backtest_accuracy(pred, by_game=False):
    """Graded 1-month calls from the base backtest vintages (model retrained
    as of a past month, later data hidden). length()=12 keeps only the
    '__bt-YYYY-MM' bases — the day-stamped display clones would triple-count."""
    where = "model_version LIKE '__bt-%' AND length(model_version) = 12 AND horizon = '1m'"
    if by_game:
        return pred.execute(ACCURACY_SELECT.format(cols="game,") + where +
                            " GROUP BY game HAVING COUNT(*) >= " + str(MIN_GRADED)).fetchall()
    return pred.execute(ACCURACY_SELECT.format(cols="") + where).fetchone()


# ---- inline SVG bar charts -------------------------------------------------
# Self-contained horizontal bars embedded in the stored report HTML. Colors
# ride the site's CSS variables (with hard fallbacks), so they follow the
# theme without any client-side chart code.
CHART_W, ROW_H, LABEL_W, CHART_PAD = 640, 26, 150, 8


def chart_title(title, w=CHART_W):
    """Small caps title inside the SVG so each chart names itself; the class
    keeps it static under the client's draw-in animation."""
    return (f"<text x='0' y='12' font-size='10' class='report-chart-title' "
            f"fill='var(--text-muted, #8b96ad)'>{esc(title.upper())}</text>")


def bar_chart(rows, unit="%", signed=True, color=None, title=None):
    """[(label, value, annotation)] -> horizontal bar SVG string.

    signed=True draws a diverging chart around a zero line (positive green,
    negative red); color forces one fill for all bars (unsigned metrics like
    error size, where green/red would editorialize).
    """
    if not rows:
        return ""
    top = 20 if title else 0
    h = ROW_H * len(rows) + CHART_PAD * 2 + top
    span = max(abs(v) for _, v, _ in rows) or 1.0
    has_neg = signed and any(v < 0 for _, v, _ in rows)
    plot_w = CHART_W - LABEL_W - 110
    zero_x = LABEL_W + (plot_w / 2 if has_neg else 0)
    scale = (plot_w / 2 if has_neg else plot_w) / span
    parts = [f"<svg class='report-chart' viewBox='0 0 {CHART_W} {h}' "
             f"role='img' xmlns='http://www.w3.org/2000/svg'>"]
    if title:
        parts.append(chart_title(title))
    if has_neg:
        parts.append(f"<line x1='{zero_x}' y1='{CHART_PAD + top}' x2='{zero_x}' y2='{h - CHART_PAD}' "
                     "stroke='var(--border, #2e3a52)'/>")
    y = CHART_PAD + top
    for label, v, note in rows:
        bw = max(abs(v) * scale, 1.0)
        x = zero_x - bw if v < 0 else zero_x
        fill = color or ("var(--down, #ff7a7a)" if v < 0 else "var(--up, #3fd98a)")
        cy = y + ROW_H / 2 + 4
        parts.append(f"<text x='{LABEL_W - 8}' y='{cy}' text-anchor='end' font-size='12' "
                     f"fill='var(--text, #e8ecf4)'>{esc(label)}</text>")
        parts.append(f"<rect x='{x:.1f}' y='{y + 5}' width='{bw:.1f}' height='{ROW_H - 10}' "
                     f"rx='2' fill='{fill}'/>")
        tx, anchor = (zero_x - bw - 6, "end") if v < 0 else (zero_x + bw + 6, "start")
        text = (f"{v:+.1f}{unit}" if signed else f"{v:.1f}{unit}") + (f"  {note}" if note else "")
        parts.append(f"<text x='{tx:.1f}' y='{cy}' text-anchor='{anchor}' font-size='11' "
                     f"fill='var(--text-muted, #8b96ad)'>{esc(text)}</text>")
        y += ROW_H
    parts.append("</svg>")
    return "".join(parts)


# Distinct per-game line colors (the site ships a single dark theme).
GAME_COLORS = ["#3d7dca", "#ffcb05", "#3fd98a", "#ff7a7a",
               "#c678dd", "#ff9e64", "#4dd0e1", "#f06292"]


def game_week_series(pc, game, names, dates):
    """% change vs the window's first snapshot, per snapshot day — the MEAN
    across this game's cards priced both days (None where too few). Mean, not
    median: most cards don't reprice on any given day, so the median is
    pinned to exactly 0 and hides the market's drift."""
    per_card = {}
    for pid, d, price in pc.execute(
            "SELECT product_id, date, price FROM graded_price_history "
            "WHERE game=? AND grade='ungraded' AND date>=? AND date<=? AND price>0",
            (game, dates[0], dates[-1])):
        if pid in names:
            per_card.setdefault(pid, {})[d] = price
    base = {pid: s[dates[0]] for pid, s in per_card.items()
            if s.get(dates[0], 0) >= MIN_BASE}
    out = []
    for d in dates:
        ratios = [per_card[pid][d] / b for pid, b in base.items() if d in per_card[pid]]
        ratios = [r for r in ratios if 1 / MAX_WEEK_RATIO <= r <= MAX_WEEK_RATIO]
        out.append((statistics.mean(ratios) - 1) * 100 if len(ratios) >= 50 else None)
    return out


def line_chart(dates, series, title=None):
    """Multi-line SVG: series = [(label, [pct-or-None per date], color)], each
    line tagged at its right edge with the label and closing value."""
    W, H, PADT, PADB, LX, RGUT = 640, 250, 12, 22, 46, 168
    if title:
        PADT += 20
        H += 20
    plot_w = W - LX - RGUT
    vals = [v for _, ys, _ in series for v in ys if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    if hi - lo < 0.5:
        hi, lo = hi + 0.25, lo - 0.25
    ys_scale = (H - PADT - PADB) / (hi - lo)
    Y = lambda v: H - PADB - (v - lo) * ys_scale
    X = lambda i: LX + plot_w * i / max(len(dates) - 1, 1)
    parts = [f"<svg class='report-chart' viewBox='0 0 {W} {H}' role='img' "
             "xmlns='http://www.w3.org/2000/svg'>"]
    if title:
        parts.append(chart_title(title, W))
    # zero line + y extremes + first/last date labels
    parts.append(f"<line x1='{LX}' y1='{Y(0):.1f}' x2='{LX + plot_w}' y2='{Y(0):.1f}' "
                 "stroke='var(--border, #2e3a52)'/>")
    for v in (lo, hi):
        parts.append(f"<text x='{LX - 6}' y='{Y(v) + 4:.1f}' text-anchor='end' font-size='10' "
                     f"fill='var(--text-muted, #8b96ad)'>{v:+.1f}%</text>")
    for i, anchor in ((0, "start"), (len(dates) - 1, "end")):
        parts.append(f"<text x='{X(i):.1f}' y='{H - 6}' text-anchor='{anchor}' font-size='10' "
                     f"fill='var(--text-muted, #8b96ad)'>{dates[i][5:]}</text>")
    # lines, then right-edge labels nudged apart so converging lines stay legible
    labels = []
    for label, ys, color in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys) if v is not None)
        if not pts:
            continue
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2'/>")
        last = next(v for v in reversed(ys) if v is not None)
        labels.append([Y(last), f"{label} {last:+.1f}%", color])
    labels.sort()
    for i in range(1, len(labels)):
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 13)
    for y, text, color in labels:
        parts.append(f"<text x='{LX + plot_w + 8}' y='{y + 4:.1f}' font-size='11' "
                     f"fill='{color}'>{esc(text)}</text>")
    parts.append("</svg>")
    return "".join(parts)


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
    baseline, latest, window_dates = week_window(pc)
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

    # Overall game movement: each game's median price index across the week's
    # daily snapshots, drawn as one line per game.
    series = []
    for i, (game, _moves) in enumerate(sorted(per_game.items())):
        ys = game_week_series(pc, game, games_live[game], window_dates)
        if any(v is not None for v in ys):
            series.append((GAMES[game]["label"], ys, GAME_COLORS[i % len(GAME_COLORS)]))
    if series:
        # steepest weekly move first, so the legend order carries information
        series.sort(key=lambda s: -abs(next(v for v in reversed(s[1]) if v is not None)))
        body.append("<h2>This week by game</h2>"
                    "<p>Average price change across each game's tracked cards, day by "
                    "day through the week.</p>"
                    + line_chart(window_dates, series, title="Price drift this week by game"))

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

    # Model report card. Live horizons appear once their first cohort has had
    # wall-clock time to mature; until then the backtest vintages (retrained
    # in the past with later data hidden, then graded) carry the record.
    body.append("<h2>Model report card</h2>"
                "<p>Every forecast the model publishes is archived, and once its "
                "target date passes it is graded against what the price actually "
                "did — misses included. Three numbers summarize the record:</p><ul>"
                "<li><strong>Typical miss</strong> — the average gap between the "
                "predicted and realized price. A 5% typical miss on a $100 card "
                "means the model's calls landed about $5 from reality.</li>"
                "<li><strong>Bias</strong> — the direction of the average error. "
                "Above zero, forecasts ran high (the model was optimistic); below "
                "zero it undershot. Near zero is best.</li>"
                "<li><strong>80% band</strong> — every forecast ships with a "
                "low&ndash;high range the model expects to contain the real price 80% "
                "of the time. This column is how often it actually did: 80% is "
                "perfect calibration, higher means the bands are cautious, lower "
                "means overconfident.</li></ul>")

    live = [(label, *row) for h, label, days in REPORT_HORIZONS
            for row in [live_accuracy(pred, h, days)] if row]
    if live:
        body.append("<table class='report-table'>"
                    "<thead><tr><th>Horizon</th><th>Graded</th><th>Typical miss</th>"
                    "<th>Bias</th><th>80% band</th></tr></thead><tbody>")
        for label, n_graded, mae, bias, hit in live:
            body.append(f"<tr><td>{label}</td><td>{n_graded:,}</td>"
                        f"<td>{(exp(mae) - 1) * 100:.1f}%</td>"
                        f"<td>{pct((exp(bias) - 1) * 100)}</td>"
                        f"<td>{hit * 100:.0f}%</td></tr>")
        body.append("</tbody></table>")
        by_game = [r for r in live_accuracy(pred, "1m", 28, by_game=True) if r[0] in GAMES]
    else:
        bt = backtest_accuracy(pred)
        by_game = [r for r in backtest_accuracy(pred, by_game=True) if r[0] in GAMES]
        if bt and bt[0]:
            body.append(f"<p>No live forecast has been out long enough to grade yet — "
                        "the first 1-month cohort matures in August 2026, 6-month in "
                        "January 2027, 12-month in July 2027; live grades take over "
                        "here as they land. Until then, the record below comes from "
                        f"backtests: the model was retrained as of May and June 2026 "
                        "with everything after hidden, and its "
                        f"{bt[0]:,} one-month calls graded against what then happened: "
                        f"typical miss {(exp(bt[1]) - 1) * 100:.1f}%, "
                        f"bias {pct((exp(bt[2]) - 1) * 100)}, "
                        f"80% band hit {bt[3] * 100:.0f}%.</p>")

    if by_game:
        by_game.sort(key=lambda r: -r[1])
        tag = "" if live else " (backtest)"
        body.append("<table class='report-table'>"
                    f"<thead><tr><th>Game</th><th>Graded{tag}</th><th>Typical miss</th>"
                    "<th>Bias</th><th>80% band</th></tr></thead><tbody>")
        for g, n_graded, mae, bias, hit in by_game:
            body.append(f"<tr><td>{esc(GAMES[g]['label'])}</td><td>{n_graded:,}</td>"
                        f"<td>{(exp(mae) - 1) * 100:.1f}%</td>"
                        f"<td>{pct((exp(bias) - 1) * 100)}</td>"
                        f"<td>{hit * 100:.0f}%</td></tr>")
        body.append("</tbody></table>")
        acc_rows = [(GAMES[g]["label"], (exp(mae) - 1) * 100, f"{hit * 100:.0f}% band")
                    for g, _n, mae, _bias, hit in sorted(by_game, key=lambda r: r[2])]
        body.append(bar_chart(acc_rows, signed=False, color="var(--primary, #3d7dca)",
                              title=f"Typical 1-month forecast miss by game{tag} — shorter is better"))

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
