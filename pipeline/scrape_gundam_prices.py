"""
Gundam current prices from PriceCharting console pages.

PriceCharting has no bulk price-guide category for the Gundam Card Game — its
sets exist only as per-set "console" pages (e.g. /console/gundam-newtype-rising)
showing Ungraded / Grade 9 / PSA 10 columns. This script crawls those pages
politely (1 req/s, same etiquette as the graded-history crawl) and emits
`pricecharting_gundam.csv` in the exact bulk-CSV format build_pricecharting.py
already consumes, with `tcg-id` filled in by OUR OWN matcher (console→set,
then card number, then variant qualifier) since console pages don't carry
TCGplayer ids.

Unmatched / ambiguous products go to ml_data/gundam_match_review.csv instead
of being guessed — better unpriced than wrongly priced.

Run:  .venv/bin/python scrape_gundam_prices.py
"""

import csv
import html as htmllib
import os
import re
import ssl
import time
import urllib.error
import urllib.request

import certifi

from _paths import DATA_DIR as BASE
import sqlite3

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

UA = "Mozilla/5.0 (tcg-predictor gundam price sync; polite 1 req/s)"
DELAY = 1.0
OUT_CSV = os.path.join(BASE, "pricecharting_gundam.csv")
REVIEW_CSV = os.path.join(BASE, "ml_data", "gundam_match_review.csv")
CARD_DB = os.path.join(BASE, "gundam_cards.db")

# The bulk price-guide header build_pricecharting.py's DictReader expects.
HEADER = ("id,console-name,product-name,loose-price,cib-price,new-price,"
          "graded-price,box-only-price,manual-only-price,bgs-10-price,"
          "condition-17-price,condition-18-price,gamestop-price,"
          "gamestop-trade-price,retail-loose-buy,retail-loose-sell,"
          "retail-cib-buy,retail-cib-sell,retail-new-buy,retail-new-sell,"
          "upc,sales-volume,genre,tcg-id,asin,epid,release-date").split(",")


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", ""))).strip("-")


# PC console slug candidates -> our set_name(s). Both starter-deck slug
# conventions exist on PC (gundam-celestial-drive AND
# gundam-starter-deck-08-flash-of-radiance), so probe both per deck.
STARTER_DECKS = {
    "01": "Heroic Beginnings", "02": "Wings of Advance", "03": "Zeon's Rush",
    "04": "SEED Strike", "05": "Iron Bloom", "06": "Clan Unity",
    "07": "Celestial Drive", "08": "Flash of Radiance",
    "09": "Destiny Ignition", "10": "Generation Pulse",
}

CANDIDATES = {
    "gundam-newtype-rising": ["Newtype Rising"],
    "gundam-edition-beta": ["Edition Beta"],
    "gundam-dual-impact": ["Dual Impact"],
    "gundam-eternal-nexus": ["Eternal Nexus"],
    "gundam-phantom-aria": ["Phantom Aria"],
    "gundam-steel-requiem": ["Steel Requiem"],
    "gundam-freedom-ascension": ["Freedom Ascension"],
    "gundam-deck-build-box-freedom-ascension": ["Deck Build Box Freedom Ascension"],
    "gundam-promo": ["Gundam Promotional Cards"],
    "gundam-resource-promo": ["Promotional Resource Tokens"],
    "gundam-ex-resource-token-promo": ["Promotional EX Resource Tokens"],
    "gundam-ex-base-token-promo": ["Promotional EX Base Tokens"],
}
for nn, sub in STARTER_DECKS.items():
    st = f"Starter Deck {nn}: {sub}"
    CANDIDATES[f"gundam-starter-deck-{nn}-{slugify(sub)}"] = [st]
    CANDIDATES[f"gundam-{slugify(sub)}"] = [st]
# Apostrophe slug variants ("Zeon's" -> zeon-s) some PC consoles use.
CANDIDATES["gundam-starter-deck-03-zeon-s-rush"] = ["Starter Deck 03: Zeon's Rush"]
CANDIDATES["gundam-zeon-s-rush"] = ["Starter Deck 03: Zeon's Rush"]


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
NUM_RE = re.compile(r"#\s*([A-Z]{2,4}\d{2}-\d{3})")

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

def norm(s):
    """Comparable form: lowercase, brackets->nothing-special, drop the #number
    echo and '(GDxx-yyy)' parens, collapse punctuation."""
    s = s.lower()
    s = re.sub(r"#\s*[a-z]{2,4}\d{2}-\d{3}", " ", s)
    s = re.sub(r"\(\s*[a-z]{2,4}\d{2}-\d{3}\s*\)", " ", s)
    s = s.replace("[", "(").replace("]", ")")
    s = re.sub(r"[^a-z0-9+()]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def variant_tokens(s):
    """The '(...)' qualifier segments of a normalized name."""
    return " ".join(re.findall(r"\(([^)]*)\)", s)).strip()


# Rarity letters PC embeds in its bracket qualifiers ('[LR+]', '[R]'), which
# carry no variant information (our rarity lives in its own column), plus
# 'holo': PC lists foil print runs as separate products ('[LR+ Holo]') while
# TCGplayer (our catalog) doesn't split them — the finish isn't a variant for
# matching. 'sp' is NOT rarity — it's a real variant marker.
RARITY_WORDS = {"lr", "sr", "r", "uc", "u", "c", "holo"}
STOP_WORDS = {"the", "of", "a", "an"}


def kinds(qualifier):
    """Classify a qualifier string into a comparable variant signature:
    a frozenset drawn from {'++', '+', 'sp'} plus any leftover event words.
    Plain rarity ('lr') or empty -> empty signature = the base printing."""
    q = qualifier.lower()
    q = re.sub(r"\balternate art\b", "+", q)      # PC's alt-art == our '+'
    out, words = set(), []
    for w in q.replace("+", " + ").split():
        if w == "+":
            out.add("+")
        elif w in RARITY_WORDS or w in STOP_WORDS:
            continue
        elif w == "sp":
            out.add("sp")
        else:
            words.append(w)
    # consecutive '+' marks ('lr++' splits to two '+') collapse via count
    plus = qualifier.count("+") + len(re.findall(r"\balternate art\b", qualifier.lower()))
    out.discard("+")
    if plus >= 2:
        out.add("++")
    elif plus == 1:
        out.add("+")
    return frozenset(out), frozenset(words)


def our_signature(card_name_norm):
    """Variant signature of OUR card: classify each paren segment; segments
    that are neither plus/sp/event markers are name subtitles ('destroy
    mode') and don't count as variants."""
    marks, words = set(), set()
    for seg in re.findall(r"\(([^)]*)\)", card_name_norm):
        m, w = kinds(seg)
        if m or w:
            marks |= m
            words |= w
        # else: pure name subtitle — ignore
    # '(sp) (lr+)' style combos keep both marks
    return frozenset(marks), frozenset(words)


def pick(pc_name, cands):
    """Choose our card among number-sharing candidates, or None if ambiguous."""
    if len(cands) == 1:
        return cands[0]
    p = norm(pc_name)
    # 1) exact normalized-name equality (handles '(Destroy Mode)' etc.)
    exact = [c for c in cands if norm(c["name"]) == p]
    if len(exact) == 1:
        return exact[0]
    # 2) variant-signature equality: marks must match exactly; PC's event
    #    words (if any) must appear in ours.
    pc_marks, pc_words = kinds(variant_tokens(p))
    hits = []
    for c in cands:
        marks, words = our_signature(norm(c["name"]))
        if marks == pc_marks and (not pc_words or pc_words <= words):
            hits.append(c)
    return hits[0] if len(hits) == 1 else None


def main():
    con = sqlite3.connect(CARD_DB)
    cards = [dict(zip(("product_id", "name", "card_number", "set_name"), r))
             for r in con.execute(
                 "SELECT product_id, name, card_number, set_name FROM cards")]
    con.close()
    by_number = {}
    for c in cards:
        by_number.setdefault(c["card_number"], []).append(c)

    matched_rows, review, seen_pc, matched_ids = [], [], set(), set()
    found_consoles = []
    for slug, sets in CANDIDATES.items():
        products = crawl_console(slug)
        if products is None:
            continue
        found_consoles.append((slug, len(products)))
        # Non-holo prints first: a '[Holo]' twin of an already-claimed product
        # is the same TCGplayer SKU seen at a different finish — skip it
        # silently rather than steal the match or spam the review file.
        for prod in sorted(products, key=lambda p: "holo" in p["name"].lower()):
            if prod["pc_id"] in seen_pc:      # deck listed under both slug styles
                continue
            m = NUM_RE.search(prod["name"])
            if not m:
                continue                       # sealed/booster products: no card number
            is_holo = "holo" in prod["name"].lower()
            cands = by_number.get(m.group(1), [])
            scoped = [c for c in cands if c["set_name"] in sets] or cands
            hit = pick(prod["name"], scoped)
            if hit is not None and hit["product_id"] in matched_ids:
                if is_holo:
                    continue                   # foil twin of a claimed print
                hit = None
            if hit is None:
                review.append((slug, prod["pc_id"], prod["name"],
                               "; ".join(f"{c['product_id']}:{c['name']}" for c in cands)
                               or "no card with this number"))
                continue
            seen_pc.add(prod["pc_id"])
            matched_ids.add(hit["product_id"])
            matched_rows.append((prod, hit, slug))

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
