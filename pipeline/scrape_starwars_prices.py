"""
Star Wars Unlimited current prices from PriceCharting console pages.

Like gundam, PriceCharting has no bulk price-guide category for this game —
its sets exist as per-set "console" pages (star-wars-unlimited-<set>), with
Ungraded / Grade 9 / PSA 10 columns. This script crawls those pages politely
(1 req/s, same etiquette as the graded-history crawl) and emits
`pricecharting_starwars.csv` in the exact bulk-CSV format
build_pricecharting.py already consumes, with `tcg-id` filled in by OUR OWN
matcher since console pages don't carry TCGplayer ids.

Matching is far simpler than gundam's: SWU collector numbers are unique
within a set AND shared between PC and TCGplayer — base printings are
"NNN/252"-style in our catalog, variant printings (Hyperspace/Showcase/
Prestige/...) carry bare numbers past the base range ("317"), and PC titles
end in the same "#317". So a product matches on (console's set, number)
alone; PC's "[Foil ...]" products are finish twins of a SKU our catalog
doesn't split (TCGplayer prices finishes inside one product) and are skipped
once the non-foil print has claimed the number.

Unmatched / ambiguous products go to ml_data/starwars_match_review.csv
instead of being guessed — better unpriced than wrongly priced.

Run:  .venv/bin/python scrape_starwars_prices.py
"""

import csv
import html as htmllib
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request

import certifi

from _paths import DATA_DIR as BASE

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

UA = "Mozilla/5.0 (tcg-predictor starwars price sync; polite 1 req/s)"
DELAY = 1.0
OUT_CSV = os.path.join(BASE, "pricecharting_starwars.csv")
REVIEW_CSV = os.path.join(BASE, "ml_data", "starwars_match_review.csv")
CARD_DB = os.path.join(BASE, "starwars_cards.db")
# Every live crawl snapshots its raw products here; --from-cache reruns the
# matcher against the snapshot so tuning it costs PriceCharting nothing.
CACHE_JSON = os.path.join(BASE, "ml_data", "starwars_pc_cache.json")

# The bulk price-guide header build_pricecharting.py's DictReader expects.
HEADER = ("id,console-name,product-name,loose-price,cib-price,new-price,"
          "graded-price,box-only-price,manual-only-price,bgs-10-price,"
          "condition-17-price,condition-18-price,gamestop-price,"
          "gamestop-trade-price,retail-loose-buy,retail-loose-sell,"
          "retail-cib-buy,retail-cib-sell,retail-new-buy,retail-new-sell,"
          "upc,sales-volume,genre,tcg-id,asin,epid,release-date").split(",")


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", ""))).strip("-")


# The main sets follow one convention (star-wars-unlimited-<set>), confirmed
# live for Spark of Rebellion. Promo sets are probed with a few slug guesses;
# ones PC doesn't have simply 404 and are reported at the end. A console maps
# to a LIST of our set_names: matching is scoped STRICTLY to those sets (SWU
# numbers repeat across sets, so an unscoped fallback would mismatch).
MAIN_SETS = [
    "Spark of Rebellion", "Shadows of the Galaxy", "Twilight of the Republic",
    "Jump to Lightspeed", "Legends of the Force", "Secrets of Power",
    "Ashes of the Empire", "A Lawless Time", "Twin Suns",
]
CANDIDATES = {f"star-wars-unlimited-{slugify(s)}": [s] for s in MAIN_SETS}
# PC drops leading articles ("A Lawless Time" -> lawless-time), so every set
# with one also probes its stripped slug. Absent slugs 404 harmlessly.
for s in MAIN_SETS:
    stripped = re.sub(r"^(?:a|an|the)\s+", "", s, flags=re.I)
    if stripped != s:
        CANDIDATES[f"star-wars-unlimited-{slugify(stripped)}"] = [s]
CANDIDATES["star-wars-unlimited-intro-battle-hoth"] = ["Intro Battle: Hoth"]
# Promo consoles, if PC has them. Each guess scopes to the promo sets it
# could plausibly hold; number collisions across those sets -> review file.
PROMO_SETS = [
    "Organized Play Promos", "Judge Promos", "Event Exclusive Promos",
    "Prerelease Promos", "Gamegenic Promos",
    "2024 Convention Exclusive", "2025 Convention Exclusive", "2025 Gift Box",
    "Sector and Regional Promos: Season 1",
]
for slug in ("star-wars-unlimited-promo", "star-wars-unlimited-promos",
             "star-wars-unlimited-organized-play-promos",
             "star-wars-unlimited-judge-promos"):
    CANDIDATES[slug] = PROMO_SETS


def fetch(url):
    time.sleep(DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""


ROW_RE = re.compile(r'<tr id="product-(\d+)"(.*?)</tr>', re.S)
TITLE_RE = re.compile(r'<td class="title"[^>]*>\s*<a[^>]*>([^<]+)</a>', re.S)
CELL_RE = re.compile(
    r'class="price numeric (used_price|cib_price|new_price)"(.*?)</td>', re.S)
PRICE_RE = re.compile(r'js-price">\s*\$([\d,]+\.?\d*)')
# PC titles end in the bare collector number: "Luke Skywalker [Hyperspace] #317"
NUM_RE = re.compile(r"#\s*(\d+)\s*$")

# Console cell class -> semantic tier on CARD console pages
# (headers read Ungraded / Grade 9 / PSA 10).
CELL_TIER = {"used_price": "ungraded", "cib_price": "grade9", "new_price": "psa10"}


def crawl_console(slug):
    """All products on one console page (cursor pagination, 150/page)."""
    products, cursor = [], 0
    while True:
        url = f"https://www.pricecharting.com/console/{slug}" + (
            f"?cursor={cursor}" if cursor else "")
        status, html = fetch(url)
        if status == 404:
            return None if cursor == 0 else products
        rows = ROW_RE.findall(html)
        for pc_id, body in rows:
            t = TITLE_RE.search(body)
            name = htmllib.unescape(t.group(1).strip()) if t else ""
            prices = {}
            for cls, cell in CELL_RE.findall(body):
                m = PRICE_RE.search(cell)
                if m:
                    prices[CELL_TIER[cls]] = m.group(1)
            products.append({"pc_id": pc_id, "name": name, **prices})
        if len(rows) < 150:
            return products
        cursor += 150


# ---------- matching ----------

def card_num(card_number):
    """Our card_number as a plain int: '179/252' -> 179, '442' -> 442.
    Token cards ('T01 // T02') and anything non-numeric -> None."""
    m = re.match(r"^\s*(\d+)\s*(?:/\d+)?\s*$", card_number or "")
    return int(m.group(1)) if m else None


# PC bracket tags that describe a finish, not a distinct catalog product: our
# TCGplayer catalog prices foils inside the same product, so a "[Foil ...]"
# console row is a twin of the non-foil print with the same number.
def is_foil(pc_name):
    return bool(re.search(r"\[[^]]*foil[^]]*\]", pc_name.lower()))


# Variant words PC puts in brackets that our catalog echoes in parens —
# used as a sanity cross-check on top of the number match.
VARIANT_WORDS = ("hyperspace", "showcase", "prestige", "serialized")


def variant_agrees(pc_name, our_name):
    """The number encodes the variant, so this only guards against the two
    numbering schemes drifting. One-directional on purpose: PC labels
    Hyperspace prints in brackets but lists Showcase/Serialized/Prestige
    prints as bare "Name #NNN" (the number range IS their label), so a
    variant word PC put in brackets must appear in our name, while our
    qualifiers that PC omitted are fine."""
    pc, ours = pc_name.lower(), our_name.lower()
    return all(w in ours for w in VARIANT_WORDS
               if re.search(rf"\[[^]]*{w}[^]]*\]", pc))


def main():
    con = sqlite3.connect(CARD_DB)
    cards = [dict(zip(("product_id", "name", "card_number", "set_name"), r))
             for r in con.execute(
                 "SELECT product_id, name, card_number, set_name FROM cards")]
    con.close()
    # (set_name, number) -> cards; numbers are unique per set today, but a
    # future collision degrades to the review file rather than a guess.
    by_set_num = {}
    for c in cards:
        n = card_num(c["card_number"])
        if n is not None:
            by_set_num.setdefault((c["set_name"], n), []).append(c)

    from_cache = "--from-cache" in sys.argv
    crawled = json.load(open(CACHE_JSON)) if from_cache else {}

    matched_rows, review, seen_pc, matched_ids = [], [], set(), set()
    found_consoles = []
    for slug, sets in CANDIDATES.items():
        products = crawled.get(slug) if from_cache else crawl_console(slug)
        if products is None:
            continue
        if not from_cache:
            crawled[slug] = products
        found_consoles.append((slug, len(products)))
        # Non-foil prints first, so the foil twin finds its number claimed and
        # is skipped silently instead of stealing the match.
        for prod in sorted(products, key=lambda p: is_foil(p["name"])):
            if prod["pc_id"] in seen_pc:
                continue
            m = NUM_RE.search(prod["name"])
            if not m:
                continue                       # sealed/booster products: no number
            n = int(m.group(1))
            cands = [c for s in sets for c in by_set_num.get((s, n), [])]
            hit = cands[0] if len(cands) == 1 else None
            if hit is not None and not variant_agrees(prod["name"], hit["name"]):
                hit = None
            if hit is not None and hit["product_id"] in matched_ids:
                if is_foil(prod["name"]):
                    continue                   # foil twin of a claimed print
                hit = None
            if hit is None:
                review.append((slug, prod["pc_id"], prod["name"],
                               "; ".join(f"{c['product_id']}:{c['name']}" for c in cands)
                               or "no card with this number in the console's sets"))
                continue
            seen_pc.add(prod["pc_id"])
            matched_ids.add(hit["product_id"])
            matched_rows.append((prod, hit, slug))

    if not from_cache:
        os.makedirs(os.path.dirname(CACHE_JSON), exist_ok=True)
        with open(CACHE_JSON, "w") as f:
            json.dump(crawled, f)

    # Emit the bulk-format CSV (atomically), tcg-id = our matched product_id.
    tmp = OUT_CSV + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for prod, hit, slug in matched_rows:
            w.writerow({
                "id": prod["pc_id"],
                "console-name": slug,
                "product-name": prod["name"],
                "loose-price": f"${prod['ungraded']}" if prod.get("ungraded") else "",
                "graded-price": f"${prod['grade9']}" if prod.get("grade9") else "",
                "manual-only-price": f"${prod['psa10']}" if prod.get("psa10") else "",
                "tcg-id": hit["product_id"],
            })
    if os.path.exists(OUT_CSV):
        os.replace(OUT_CSV, OUT_CSV + ".prev")
    os.replace(tmp, OUT_CSV)

    os.makedirs(os.path.dirname(REVIEW_CSV), exist_ok=True)
    with open(REVIEW_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["console", "pc_id", "pc_name", "candidates"])
        w.writerows(review)

    print("consoles found:")
    for slug, n in found_consoles:
        print(f"  {slug}: {n} products")
    missing = [s for s in CANDIDATES if s not in {x for x, _ in found_consoles}]
    print(f"consoles not on PC: {len(missing)} probed slugs")
    print(f"matched {len(matched_rows)} products -> {os.path.basename(OUT_CSV)} | "
          f"unmatched/ambiguous: {len(review)} -> {os.path.basename(REVIEW_CSV)}")


if __name__ == "__main__":
    main()
