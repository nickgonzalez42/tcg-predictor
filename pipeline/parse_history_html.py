#!/usr/bin/env python3
"""
Fallback parser for TCGplayer's price-history chart.

The price chart on a product page renders a hidden accessibility <table> inside
its <canvas> (data-testid="History__Line"). Each row is a 3-day bucket:

    <td>3/18 to 3/20</td><td>$36.00</td><td>$18.00</td>
       date range          market price    quantity sold (count, $-formatted)

The table omits the YEAR, so we reconstruct it: assign a reference year to the
most-recent bucket and walk backwards, rolling the year back whenever the month
jumps up (a Dec -> Jan boundary going backward in time).

Use this only if the JSON endpoint
(infinite-api.tcgplayer.com/price/history/{id}/detailed) is unavailable -- the
JSON gives the same data plus low/high sale prices and explicit ISO dates.

NOTE: obtaining this HTML requires a headless browser (Selenium / Playwright),
because the table is injected by Chart.js after the page's JS runs. Plain
requests will not see it.

Usage:
    from parse_history_html import parse_price_history_html
    rows = parse_price_history_html(html, ref_year=2026)
    # -> [{'start_date':'2026-03-18','end_date':'2026-03-20',
    #      'market_price':36.0,'quantity_sold':18.0}, ...]
"""

import re
from datetime import date


_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_RANGE_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*to\s*(\d{1,2})/(\d{1,2})")
_TAG_RE = re.compile(r"<[^>]+>")


def _money_to_float(text):
    """'$36.00' / '18.00' / '$1,234' -> float, or None."""
    if text is None:
        return None
    cleaned = _TAG_RE.sub("", text)
    cleaned = cleaned.replace("$", "").replace(",", "").strip()
    if cleaned in ("", "-", "—"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_price_history_html(html, ref_year=None):
    """Parse a TCGplayer History__Line chart table into dated buckets.

    Args:
        html: the chart HTML (or any HTML containing the <table>).
        ref_year: year the most-recent bucket falls in. Defaults to today's
                  year. Set this if you saved the HTML in a different year.

    Returns:
        List of dicts (oldest-first), each:
            start_date, end_date  -> 'YYYY-MM-DD'
            market_price          -> float | None
            quantity_sold         -> float | None
    """
    if ref_year is None:
        ref_year = date.today().year

    # Collect raw rows: (start_m, start_d, end_m, end_d, market, qty)
    raw = []
    for row_html in _ROW_RE.findall(html):
        cells = _CELL_RE.findall(row_html)
        if not cells:
            continue  # header row uses <th>
        m = _RANGE_RE.search(_TAG_RE.sub("", cells[0]))
        if not m:
            continue
        sm, sd, em, ed = (int(g) for g in m.groups())
        market = _money_to_float(cells[1]) if len(cells) > 1 else None
        qty = _money_to_float(cells[2]) if len(cells) > 2 else None
        raw.append((sm, sd, em, ed, market, qty))

    # Reconstruct years by walking backward from the most recent bucket.
    out = []
    year = ref_year
    prev_start_month = None
    for sm, sd, em, ed, market, qty in reversed(raw):
        if prev_start_month is not None and sm > prev_start_month:
            year -= 1                       # crossed a Dec -> Jan boundary
        start_year = year
        end_year = year + 1 if em < sm else year   # bucket spans New Year
        out.append({
            "start_date": f"{start_year:04d}-{sm:02d}-{sd:02d}",
            "end_date": f"{end_year:04d}-{em:02d}-{ed:02d}",
            "market_price": market,
            "quantity_sold": qty,
        })
        prev_start_month = sm

    out.reverse()  # oldest-first
    return out


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            html_in = f.read()
    else:
        html_in = sys.stdin.read()
    ref = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(parse_price_history_html(html_in, ref_year=ref), indent=2))
