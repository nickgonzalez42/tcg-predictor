#!/usr/bin/env python3
"""
Discover NEW PriceCharting console (set) pages, so a set released after the
paid bulk CSV ended (2026-07-31) still gets linked and graded automatically.

Background: the public console sweep (scrape_pc_prices.py) crawls only the
consoles already present in our match table, which came from the paid CSV.
A set PriceCharting added since then — every set released after July 2026 —
was invisible to it, so none of those cards ever reached pc_link_suggest or
the graded crawl. This step closes that loop:

  * Bulk-category games (pokemon, one piece, yugioh, magic, lorcana, digimon):
    PriceCharting's category page lists every console it tracks for the game.
    Read it, keep the game's own English consoles, and diff against what we
    already know (match table + earlier discoveries).
  * Console-scraped games (gundam, star wars): no category page exists, so
    probe the slug PriceCharting would use for each of OUR sets that has no
    link yet (prefix + slugified set name, with the leading article dropped
    as a second guess). A 200 is a discovery; a 404 costs one request.

Output: ml_data/pc_consoles_discovered.csv (game, slug, name, discovered_at).
scrape_pc_prices.py crawls these alongside the known consoles; their products
land in the review CSV; pc_link_suggest reads each page's embedded tcg-id and
queues exact links; build_pricecharting applies them with the console name;
the graded crawl then picks the new cards up. Runs on the full-refresh night,
just before the sweep. Always exits 0.
"""

import argparse
import csv
import html as H
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE   # noqa: E402
from games import GAMES, priced_games, db_path   # noqa: E402
from scrape_gundam_prices import fetch, slugify   # noqa: E402

PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
OUT_CSV = os.path.join(BASE, "ml_data", "pc_consoles_discovered.csv")
SLUG_OVERRIDES_CSV = os.path.join(BASE, "ml_data", "pc_console_slugs.csv")

# PriceCharting's slug prefix for each category game (its category page also
# carries a site-wide console nav — NES, Wii, ... — which the prefix drops).
PREFIX = {
    "pokemon": "pokemon-", "onepiece": "one-piece-", "yugioh": "yugioh-",
    "magic": "magic-", "lorcana": "lorcana-", "digimon": "digimon-",
}
# Console-scraped games: the slug PriceCharting uses for a set.
PROBE_PREFIX = {"gundam": "gundam-", "starwars": "star-wars-unlimited-"}
# Foreign-language and non-single consoles are never our catalog (TCGplayer
# lists English singles); skipping them keeps the sweep + link queue focused.
SKIP_RE = re.compile(
    r"japanese|chinese|korean|german|french|italian|spanish|portuguese|thai|"
    r"indonesian|russian|polish|dutch|latin-america|brazil|sealed|graded-", re.I)
CONSOLE_LINK_RE = re.compile(r'<a[^>]*href="/console/([a-z0-9-]+)"[^>]*>\s*([^<]{1,120}?)\s*</a>')


def parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def known_slugs(conn, game):
    """Slugs of consoles already in the match table (display names slugified
    the same way the sweep does, plus the hand-kept overrides)."""
    slugs = set()
    overrides = {}
    if os.path.exists(SLUG_OVERRIDES_CSV):
        with open(SLUG_OVERRIDES_CSV, newline="", encoding="utf-8") as f:
            overrides = {r["console"]: r["slug"] for r in csv.DictReader(f) if r["game"] == game}
    for (con,) in conn.execute(
            "SELECT DISTINCT pc_console FROM pricecharting WHERE game=? AND pc_console IS NOT NULL",
            (game,)):
        slugs.add(overrides.get(con) or slugify(con))
    return slugs


def load_discovered():
    rows = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["game"], r["slug"])] = r
    return rows


def save_discovered(rows):
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    tmp = OUT_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["game", "slug", "name", "discovered_at"])
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in w.fieldnames})
    os.replace(tmp, OUT_CSV)


def category_consoles(game):
    """[(slug, name)] for the game's own English consoles on its category page."""
    status, page = fetch(f"https://www.pricecharting.com/category/{GAMES[game]['pc_category']}")
    if status != 200 or not page:
        print(f"[{game}] category page unavailable (HTTP {status})", file=sys.stderr)
        return None
    prefix = PREFIX[game]
    out, seen = [], set()
    for slug, name in CONSOLE_LINK_RE.findall(page):
        if not slug.startswith(prefix) or slug in seen or SKIP_RE.search(slug):
            continue
        seen.add(slug)
        out.append((slug, H.unescape(name).strip()))
    return out


def unlinked_sets(conn, game, today):
    """Our released sets (console-scraped games) with no PriceCharting link."""
    cards = sqlite3.connect(f"file:{db_path(game)}?mode=ro", uri=True, timeout=60)
    cols = [r[1] for r in cards.execute("PRAGMA table_info(cards)")]
    linked = {pid for (pid,) in conn.execute(
        "SELECT product_id FROM pricecharting WHERE game=? AND pc_id IS NOT NULL", (game,))}
    sets = {}
    rel_col = "substr(release_date,1,10)" if "release_date" in cols else "NULL"
    for pid, s, rel in cards.execute(f"SELECT product_id, set_name, {rel_col} FROM cards WHERE set_name IS NOT NULL"):
        a = sets.setdefault(s, {"n": 0, "linked": 0, "rel": None})
        a["n"] += 1
        a["linked"] += pid in linked
        d = parse_date(rel)
        if d and (a["rel"] is None or d < a["rel"]):
            a["rel"] = d
    cards.close()
    return [s for s, a in sets.items()
            if a["linked"] == 0 and a["n"] >= 10 and (a["rel"] is None or a["rel"] <= today)]


def probe_slugs(set_name, prefix):
    base = slugify(set_name)
    cands = [f"{prefix}{base}"]
    stripped = re.sub(r"^(?:a|an|the)\s+", "", set_name, flags=re.I)
    if stripped != set_name:
        cands.append(f"{prefix}{slugify(stripped)}")
    # "Starter Deck 11: Aquatic Assault" -> both the full slug and the subtitle
    if ":" in set_name:
        cands.append(f"{prefix}{slugify(set_name.split(':', 1)[1])}")
    return list(dict.fromkeys(cands))


def main():
    ap = argparse.ArgumentParser(description="Discover new PriceCharting console pages per game.")
    ap.add_argument("--game", action="append")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(f"file:{PC_DB}?mode=ro", uri=True, timeout=60)
    discovered = load_discovered()
    total_new = 0
    for game in (args.game or priced_games()):
        known = known_slugs(conn, game) | {k[1] for k in discovered if k[0] == game}
        new = []
        if game in PREFIX and GAMES[game].get("pc_category"):
            found = category_consoles(game)
            if found is None:
                continue
            new = [(slug, name) for slug, name in found if slug not in known]
            print(f"[{game}] category lists {len(found)} English consoles; {len(known)} known; "
                  f"{len(new)} new" + (f": {', '.join(s for s, _ in new[:8])}" if new else ""))
        elif game in PROBE_PREFIX:
            sets = unlinked_sets(conn, game, today)
            probed = 0
            for s in sets:
                for slug in probe_slugs(s, PROBE_PREFIX[game]):
                    if slug in known:
                        break
                    probed += 1
                    status, page = fetch(f"https://www.pricecharting.com/console/{slug}")
                    if status == 200 and 'id="product-' in page:
                        new.append((slug, s))
                        break
            print(f"[{game}] {len(sets)} unlinked sets probed ({probed} requests); {len(new)} new console(s)"
                  + (f": {', '.join(s for s, _ in new)}" if new else ""))
        for slug, name in new:
            discovered[(game, slug)] = {"game": game, "slug": slug, "name": name, "discovered_at": now_iso}
        total_new += len(new)
    conn.close()
    save_discovered(discovered)
    print(f"pc_console_discover: {total_new} new console(s); {len(discovered)} on file -> {OUT_CSV}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # noqa: BLE001 — discovery must never fail the night
        print(f"pc_console_discover: non-fatal error: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
