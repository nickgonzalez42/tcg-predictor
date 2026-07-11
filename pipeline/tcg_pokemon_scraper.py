#!/usr/bin/env python3
"""
TCGplayer Pokemon Trading Card Game scraper.

Collects EVERY Pokemon card from TCGplayer's public storefront endpoints:
  - Full card info (name, set, rarity, number, HP, stage, type, attacks, etc.)
  - Card image (downloaded to disk)
  - Maximum-available detailed historical price data (per variant/printing)

Data is written to a SQLite database. The run is resumable: re-running skips
products whose price history has already been fetched.

This is the Pokemon twin of tcg_onepiece_scraper.py -- same engine (rate
limiter, retry/backoff, resumable SQLite storage), differing only in the
product-line slug and the per-card attribute mapping.

------------------------------------------------------------------------------
IMPORTANT / LEGAL
------------------------------------------------------------------------------
Scraping TCGplayer is against their Terms of Service, and they run bot
protection (Cloudflare). This script hits the same JSON endpoints the public
website calls, with conservative rate limiting. Use it responsibly, for
personal/research use only, at your own risk. If you need this data
commercially, pursue an official TCGplayer / TCGplayer Infinite data
partnership instead.

These endpoints are undocumented and can change without notice. If a request
shape stops working, inspect your browser's Network tab on tcgplayer.com to
see the current payloads, and update the constants below.
------------------------------------------------------------------------------

Usage:
    pip install requests
    python tcg_pokemon_scraper.py                 # full run
    python tcg_pokemon_scraper.py --limit 50      # quick test (first 50 cards)
    python tcg_pokemon_scraper.py --no-images     # skip image download
    python tcg_pokemon_scraper.py --delay 1.5     # seconds between requests

Requires only the `requests` library from PyPI.
"""

import argparse
import gzip
import json
import os
import random
import sqlite3
import sys
import threading
import time
import zlib
from datetime import datetime, timezone

import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

PRODUCT_LINE = "pokemon"               # TCGplayer product-line url slug
SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
PRICE_HISTORY_URL = "https://infinite-api.tcgplayer.com/price/history/{pid}/detailed"
# Detailed history range. "annual" is the longest range the endpoint exposes.
PRICE_HISTORY_RANGE = "annual"
# CDN image template. 1000x1000 is the largest standard size.
IMAGE_URL = "https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg"

PAGE_SIZE = 50               # results per search request (API rejects > 50 with HTTP 400)
DEFAULT_DELAY = 1.0          # base seconds between requests (be polite)
DEFAULT_RPM = 30             # hard ceiling: max requests per rolling minute
MAX_RETRIES = 8              # higher, since a continuous run should self-heal
MAX_BACKOFF = 300            # cap a single backoff wait at 5 minutes
TIMEOUT = 30

from _paths import DATA_DIR
DB_PATH = os.path.join(DATA_DIR, "pokemon_cards.db")
IMAGE_DIR = os.path.join(DATA_DIR, "images_pokemon")

# Browser-like headers. Cloudflare inspects these; keep them realistic.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    # gzip only: the deflate/br paths produced "failed to decode" errors on
    # some responses (and no brotli decoder is installed), so we avoid them.
    "Accept-Encoding": "gzip",
    "Origin": "https://www.tcgplayer.com",
    "Referer": "https://www.tcgplayer.com/search/pokemon/product",
    "Content-Type": "application/json",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


# ----------------------------------------------------------------------------
# Rate limiting
# ----------------------------------------------------------------------------

class RateLimiter:
    """Token-bucket limiter enforcing a hard requests-per-minute ceiling.

    Every outbound request must call ``acquire()`` first, which blocks until a
    token is available. Tokens refill continuously at ``rpm / 60`` per second,
    so the script can run indefinitely without ever exceeding ``rpm`` requests
    in any rolling minute. A minimum spacing between consecutive requests is
    also enforced so calls aren't bursted back-to-back.
    """

    def __init__(self, rpm, min_interval=0.0, burst=1):
        # refill_rate is the steady-state rate; `burst` caps how many requests
        # can ever bunch together. Keeping burst small means the rolling-minute
        # total never meaningfully exceeds rpm (no big cold-start spike).
        self.refill_rate = max(rpm, 1) / 60.0  # tokens per second
        self.capacity = float(max(1, burst))
        self.tokens = self.capacity
        self.min_interval = min_interval
        self.last_request = 0.0
        self._last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self._last_refill) * self.refill_rate,
                )
                self._last_refill = now
                spacing_wait = self.min_interval - (now - self.last_request)
                if self.tokens >= 1.0 and spacing_wait <= 0:
                    self.tokens -= 1.0
                    self.last_request = now
                    return
                token_wait = 0.0 if self.tokens >= 1.0 \
                    else (1.0 - self.tokens) / self.refill_rate
                time.sleep(max(token_wait, spacing_wait, 0.02))

    def penalize(self, seconds):
        """After a 429/server error, drain tokens so we genuinely slow down."""
        with self.lock:
            self.tokens = 0.0
            self.last_request = time.monotonic() + seconds


# Module-level limiter; configured in main().
RATE_LIMITER = None


# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _decode_body(raw, content_encoding):
    """Decompress a response body ourselves, tolerating mislabeled encodings.

    TCGplayer's API sometimes returns gzip-compressed bodies tagged
    ``Content-Encoding: deflate``; urllib3 trusts the tag, runs the deflate
    decoder, and raises "failed to decode" (and retrying never helps because
    the bytes never change). We instead try the decoders in a sensible order
    for the advertised encoding and fall back to handing the bytes back as-is.
    """
    if not raw:
        return raw
    enc = (content_encoding or "").lower()

    def _gunzip(b):
        return gzip.decompress(b)

    def _zlib(b):
        return zlib.decompress(b)

    def _raw_deflate(b):
        return zlib.decompress(b, -zlib.MAX_WBITS)

    if "gzip" in enc:
        order = (_gunzip, _zlib, _raw_deflate)
    elif "deflate" in enc:
        # Try real (zlib-wrapped) deflate, then raw deflate, then gzip -- the
        # last covers the mislabel where "deflate" is actually gzip bytes.
        order = (_zlib, _raw_deflate, _gunzip)
    else:
        order = ()  # identity / unset -> body is already plaintext

    for fn in order:
        try:
            return fn(raw)
        except Exception:
            continue
    return raw


def _fetch(session, method, url, **kwargs):
    """session.request(), but with our own body decoding (see _decode_body).

    We stream so urllib3 doesn't auto-decode (and choke on the mislabeled
    deflate), read the raw bytes, decode them ourselves, and stash the result
    so resp.json()/.text/.content all work normally downstream.
    """
    kwargs.setdefault("stream", True)
    resp = session.request(method, url, **kwargs)
    raw = resp.raw.read(decode_content=False)
    resp._content = _decode_body(raw, resp.headers.get("Content-Encoding"))
    resp._content_consumed = True
    return resp


def _retry_after_seconds(resp, fallback):
    """Respect a Retry-After header (seconds or HTTP-date) if present."""
    val = resp.headers.get("Retry-After")
    if not val:
        return fallback
    try:
        return float(val)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(val)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return fallback


def request_with_retries(session, method, url, base_delay, retry_forbidden=True,
                         **kwargs):
    """Issue a request through the global rate limiter, with backoff.

    The rate limiter guarantees we never exceed the configured RPM. On 429 /
    5xx responses we additionally honor Retry-After and apply exponential
    backoff, then keep going -- so a long unattended run self-heals.

    ``retry_forbidden`` controls 403 handling. On the search/history APIs a 403
    can mean a transient Cloudflare block worth retrying. On the image CDN,
    however, a 403 means the product simply has no image -- retrying just burns
    ~9 minutes of backoff per imageless card -- so callers pass False there to
    give up immediately.

    With TCG_PATIENT=1 in the environment (set by the multi-day backfill),
    NETWORK failures never exhaust the attempt budget: after each exhausted
    round the function waits five minutes and starts a fresh round, so an
    internet outage pauses the crawl instead of making it skip work. HTTP
    responses (403/404/429 handling) are unaffected -- patience only applies
    when no response arrives at all.
    """
    retry_statuses = {429} if not retry_forbidden else {403, 429}
    patient = bool(os.environ.get("TCG_PATIENT"))
    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1
        if RATE_LIMITER is not None:
            RATE_LIMITER.acquire()
        try:
            resp = _fetch(session, method, url, timeout=TIMEOUT, **kwargs)
            if resp.status_code == 200:
                return resp
            if resp.status_code in retry_statuses or resp.status_code >= 500:
                backoff = base_delay * (2 ** attempt) + random.uniform(0, 2)
                wait = min(_retry_after_seconds(resp, backoff), MAX_BACKOFF)
                if RATE_LIMITER is not None:
                    RATE_LIMITER.penalize(wait)
                print(f"  [{resp.status_code}] backing off {wait:.1f}s "
                      f"(attempt {attempt}/{MAX_RETRIES}) {url}", file=sys.stderr)
                time.sleep(wait)
                continue
            # Non-retryable (e.g. 404 for a product with no history)
            return resp
        # Anything that prevents a complete response is a retryable network
        # error — bare urllib3 errors leak past requests' wrappers when the
        # BODY read times out after a 200 header, so catch everything here.
        except Exception as e:
            wait = min(base_delay * (2 ** attempt) + random.uniform(0, 2),
                       MAX_BACKOFF)
            print(f"  [error] {type(e).__name__}: {e} -> retry in {wait:.1f}s "
                  f"(attempt {attempt}/{MAX_RETRIES})", file=sys.stderr)
            time.sleep(wait)
            if patient and attempt >= MAX_RETRIES:
                print(f"  [patient] network still down — waiting 5 min, then a "
                      f"fresh round: {url}", file=sys.stderr)
                time.sleep(300)
                attempt = 0
    print(f"  [give up] {url}", file=sys.stderr)
    return None


# ----------------------------------------------------------------------------
# Search / catalog enumeration
# ----------------------------------------------------------------------------

def build_search_payload(offset, size, singles_only=True, set_name=None):
    """Mirror the storefront's product-search request body.

    When ``set_name`` is given, results are restricted to that set -- this is
    how we sidestep the API's ~10k deep-pagination cap (see iter_all_products).
    """
    term = {"productLineName": [PRODUCT_LINE]}
    if singles_only:
        # Same filter the storefront applies via ?ProductTypeName=Cards
        term["productTypeName"] = ["Cards"]
    if set_name is not None:
        term["setName"] = [set_name]
    return {
        "algorithm": "sales_dedupe_v2",
        "from": offset,
        "size": size,
        "filters": {
            "term": term,
            "range": {},
            "match": {},
        },
        "listingSearch": {
            "context": {"cart": {}},
            "filters": {
                "term": {"sellerStatus": "Live", "channelId": 0},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
        },
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {"field": "product-sorting-name", "order": "asc"},
    }


def is_single_card(product):
    """True for individual cards, False for sealed/other products.

    The search results carry no productTypeName, so we use the signals that
    actually distinguish them: individual cards have a card `number`
    (e.g. "91/119") and a real rarity ("Common", "Rare Holo", ...). Sealed
    products (booster boxes, packs, decks, cases) have number=null and
    rarityName="None".
    """
    ca = product.get("customAttributes") or {}
    if ca.get("number") not in (None, ""):
        return True
    rarity = (product.get("rarityName") or ca.get("rarityDbName") or "").strip()
    return bool(rarity) and rarity.lower() != "none"


def fetch_set_list(session, base_delay, singles_only=True):
    """Return [(set_name, count), ...] from the search setName aggregation.

    A single search response carries an ``aggregations.setName`` facet listing
    every set in the product line with its card count -- we use that as the
    partition key for enumeration.
    """
    payload = build_search_payload(0, 1, singles_only=singles_only)
    resp = request_with_retries(
        session, "POST", SEARCH_URL, base_delay,
        params={"q": "", "isList": "false"}, data=json.dumps(payload),
    )
    if resp is None or resp.status_code != 200:
        return []
    try:
        block = (resp.json().get("results") or [{}])[0]
    except ValueError:
        return []
    out = []
    for s in block.get("aggregations", {}).get("setName", []):
        value = s.get("value")
        if value:
            out.append((value, int(s.get("count") or 0)))
    return out


def _iter_query_pages(session, base_delay, singles_only, set_name):
    """Yield products for one query (a single set, or the whole line if
    ``set_name`` is None), paginating by offset.

    A failure here aborts only this query, not the whole run, so one bad set
    can't sink the rest of the catalog.
    """
    offset = 0
    label = f"set '{set_name}'" if set_name else "catalog"
    while True:
        payload = build_search_payload(offset, PAGE_SIZE,
                                       singles_only=singles_only,
                                       set_name=set_name)
        resp = request_with_retries(
            session, "POST", SEARCH_URL, base_delay,
            params={"q": "", "isList": "false"}, data=json.dumps(payload),
        )
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "no response"
            print(f"  Search failed for {label} at offset {offset}: {code}; "
                  f"skipping rest of {label}.", file=sys.stderr)
            return
        try:
            block = (resp.json().get("results") or [{}])[0]
        except ValueError:
            print(f"  Non-JSON for {label} at offset {offset}; skipping rest.",
                  file=sys.stderr)
            return
        results = block.get("results", [])
        if not results:
            return
        for product in results:
            # The search API returns productId as a float (e.g. 288228.0).
            # Coerce to int so it never leaks into a URL as "288228.0", which
            # the image CDN 403s on (and pollutes the stored image_url).
            pid = product.get("productId")
            if pid is not None:
                try:
                    product["productId"] = int(float(pid))
                except (TypeError, ValueError):
                    pass
            yield product
        offset += len(results)
        if offset >= (block.get("totalResults") or 0):
            return


def iter_all_products(session, base_delay, limit=None, singles_only=True):
    """Yield every product dict for the Pokemon product line.

    The search API caps simple offset pagination at ~10k results (HTTP 400 once
    the from+size window exceeds ~10000). Pokemon has ~29k cards, so a flat
    listing can't reach them all. Instead we read the setName aggregation and
    paginate within each set -- every set is far under the cap, and the sets
    together cover the whole catalog with no gaps or overlap.
    """
    sets = fetch_set_list(session, base_delay, singles_only=singles_only)
    fetched = 0
    if not sets:
        print("Could not read set aggregation; falling back to flat pagination "
              "(capped at ~10k results).", file=sys.stderr)
        for product in _iter_query_pages(session, base_delay, singles_only, None):
            yield product
            fetched += 1
            if limit is not None and fetched >= limit:
                return
        return
    total = sum(c for _, c in sets)
    print(f"Total Pokemon products: {total} across {len(sets)} sets.")
    for set_name, _count in sets:
        for product in _iter_query_pages(session, base_delay, singles_only,
                                         set_name):
            yield product
            fetched += 1
            if limit is not None and fetched >= limit:
                return


def iter_new_products(session, base_delay, conn, limit=None, singles_only=True):
    """Yield products only from sets whose product count moved since the last
    recorded complete scan — one aggregation request when nothing changed.

    All pricing comes from PriceCharting now, so an existing card's TCGplayer
    listing carries nothing worth re-reading: an unchanged set count means no
    new cards. A changed (or never-scanned) set is re-scanned in full — new
    cards get picked up, its existing cards get a free detail refresh — and
    its count is recorded only once every page arrived, so an aborted scan is
    retried next run.
    """
    sets = fetch_set_list(session, base_delay, singles_only=singles_only)
    if not sets:
        print("Could not read the set aggregation; falling back to a full "
              "catalog enumeration.", file=sys.stderr)
        yield from iter_all_products(session, base_delay, limit=limit,
                                     singles_only=singles_only)
        return

    known = dict(conn.execute(
        "SELECT set_name, product_count FROM set_counts").fetchall())
    todo = [(s, c) for s, c in sets if known.get(s) != c]
    print(f"Sets with changed product counts: {len(todo)} of {len(sets)}.")

    fetched = 0
    for set_name, count in todo:
        got = 0
        for product in _iter_query_pages(session, base_delay, singles_only,
                                         set_name):
            got += 1
            fetched += 1
            yield product
            if limit is not None and fetched >= limit:
                return   # testing cut-off: leave this set's count unrecorded
        if got >= count:
            conn.execute("INSERT OR REPLACE INTO set_counts VALUES (?,?,?)",
                         (set_name, count,
                          datetime.now(timezone.utc).isoformat()))
            conn.commit()
        else:
            print(f"  Set '{set_name}': {got}/{count} products arrived — "
                  f"will re-scan next run.", file=sys.stderr)


# ----------------------------------------------------------------------------
# Price history
# ----------------------------------------------------------------------------

def fetch_price_history(session, product_id, base_delay):
    """Return the detailed price-history JSON for a product, or None."""
    url = PRICE_HISTORY_URL.format(pid=product_id)
    resp = request_with_retries(
        session, "GET", url, base_delay,
        params={"range": PRICE_HISTORY_RANGE},
        headers={"Referer": f"https://www.tcgplayer.com/product/{product_id}"},
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Images
# ----------------------------------------------------------------------------

def download_image(session, product_id, base_delay, image_dir):
    """Download a card image to disk. Returns local path or None.

    Never raises: body reads can time out AFTER a 200 header (urllib3 errors
    that request_with_retries can't see), and one lost image must not kill a
    multi-hour catalog run — it's simply retried on a later run.
    """
    os.makedirs(image_dir, exist_ok=True)
    path = os.path.join(image_dir, f"{product_id}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    url = IMAGE_URL.format(pid=product_id)
    try:
        # A 403 here means the product has no CDN image; don't retry (see note
        # in request_with_retries) -- just skip it.
        resp = request_with_retries(session, "GET", url, base_delay,
                                    retry_forbidden=False)
        if resp is None or resp.status_code != 200:
            return None
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception as e:
        print(f"  [image {product_id}] {type(e).__name__} — skipped this run",
              file=sys.stderr)
        if os.path.exists(path):
            os.remove(path)   # never leave a truncated file that reads as "has art"
        return None


# ----------------------------------------------------------------------------
# SQLite storage
# ----------------------------------------------------------------------------

def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            product_id        INTEGER PRIMARY KEY,
            name              TEXT,
            clean_name        TEXT,
            set_name          TEXT,
            set_url_name      TEXT,
            rarity            TEXT,
            card_number       TEXT,
            hp                TEXT,
            stage             TEXT,
            card_type         TEXT,     -- cardType  (e.g. ["Fire"], ["Supporter"])
            card_type_b       TEXT,     -- cardTypeB (scalar variant)
            energy_type       TEXT,
            attack1           TEXT,
            attack2           TEXT,
            attack3           TEXT,
            attack4           TEXT,
            weakness          TEXT,
            resistance        TEXT,
            retreat_cost      TEXT,
            description       TEXT,
            flavor_text       TEXT,
            detail_note       TEXT,
            release_date      TEXT,
            product_url       TEXT,
            image_url         TEXT,
            image_path        TEXT,
            market_price      REAL,
            lowest_price      REAL,
            lowest_price_ship REAL,
            total_listings    INTEGER,
            custom_attributes TEXT,   -- full JSON blob of customAttributes
            raw_json          TEXT,   -- full raw product JSON
            scraped_at        TEXT,
            history_fetched   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id    INTEGER,
            variant       TEXT,       -- e.g. Normal, Holofoil, Reverse Holofoil
            condition     TEXT,       -- e.g. Near Mint, Lightly Played
            language      TEXT,
            bucket_date   TEXT,
            market_price  REAL,
            quantity_sold REAL,
            low_sale      REAL,
            low_sale_ship REAL,
            high_sale     REAL,
            trans_count   REAL,
            UNIQUE(product_id, variant, condition, language, bucket_date)
        );

        CREATE INDEX IF NOT EXISTS idx_hist_product ON price_history(product_id);
        CREATE INDEX IF NOT EXISTS idx_hist_date    ON price_history(bucket_date);

        -- Per-set product counts at the last COMPLETE scan of that set, keyed by
        -- the search aggregation's facet value. --new-only compares fresh
        -- aggregation counts against these to decide which sets to re-scan.
        CREATE TABLE IF NOT EXISTS set_counts (
            set_name      TEXT PRIMARY KEY,
            product_count INTEGER NOT NULL,
            checked_at    TEXT
        );
        """
    )
    # Migration: add history_fetched_at to older DBs that predate incremental
    # refresh. Records when each card's price history was last pulled, so a
    # weekly run can skip cards it has already refreshed recently.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()]
    if "history_fetched_at" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN history_fetched_at TEXT")
    conn.commit()


def _attr(ca, *keys):
    """Pull the first present key from a customAttributes dict.

    customAttributes values are sometimes lists (e.g. cardType=["Fire"]); those
    are flattened to a comma-joined string.
    """
    for k in keys:
        if isinstance(ca, dict) and ca.get(k) not in (None, "", []):
            v = ca.get(k)
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return v
    return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def upsert_card(conn, product, image_path):
    ca = product.get("customAttributes") or {}
    pid = product.get("productId")
    set_name = product.get("setName") or product.get("setUrlName")
    url_slug = product.get("productUrlName")
    set_slug = product.get("setUrlName")
    product_url = None
    if pid and url_slug and set_slug:
        product_url = (f"https://www.tcgplayer.com/product/{pid}/"
                       f"{PRODUCT_LINE}-{set_slug}-{url_slug}")

    conn.execute(
        """
        INSERT INTO cards (
            product_id, name, clean_name, set_name, set_url_name, rarity,
            card_number, hp, stage, card_type, card_type_b, energy_type,
            attack1, attack2, attack3, attack4, weakness, resistance,
            retreat_cost, description, flavor_text, detail_note, release_date,
            product_url, image_url, image_path, market_price, lowest_price,
            lowest_price_ship, total_listings, custom_attributes, raw_json,
            scraped_at, history_fetched
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                  COALESCE((SELECT history_fetched FROM cards WHERE product_id=?),0))
        ON CONFLICT(product_id) DO UPDATE SET
            name=excluded.name,
            clean_name=excluded.clean_name,
            set_name=excluded.set_name,
            set_url_name=excluded.set_url_name,
            rarity=excluded.rarity,
            card_number=excluded.card_number,
            hp=excluded.hp,
            stage=excluded.stage,
            card_type=excluded.card_type,
            card_type_b=excluded.card_type_b,
            energy_type=excluded.energy_type,
            attack1=excluded.attack1,
            attack2=excluded.attack2,
            attack3=excluded.attack3,
            attack4=excluded.attack4,
            weakness=excluded.weakness,
            resistance=excluded.resistance,
            retreat_cost=excluded.retreat_cost,
            description=excluded.description,
            flavor_text=excluded.flavor_text,
            detail_note=excluded.detail_note,
            release_date=excluded.release_date,
            product_url=excluded.product_url,
            image_url=excluded.image_url,
            image_path=COALESCE(excluded.image_path, cards.image_path),
            market_price=excluded.market_price,
            lowest_price=excluded.lowest_price,
            lowest_price_ship=excluded.lowest_price_ship,
            total_listings=excluded.total_listings,
            custom_attributes=excluded.custom_attributes,
            raw_json=excluded.raw_json,
            scraped_at=excluded.scraped_at
        """,
        (
            pid,
            product.get("productName"),
            product.get("cleanName"),
            set_name,
            set_slug,
            product.get("rarityName") or _attr(ca, "rarityDbName"),
            _attr(ca, "number"),
            _attr(ca, "hp"),
            _attr(ca, "stage"),
            _attr(ca, "cardType"),
            _attr(ca, "cardTypeB"),
            _attr(ca, "energyType"),
            _attr(ca, "attack1"),
            _attr(ca, "attack2"),
            _attr(ca, "attack3"),
            _attr(ca, "attack4"),
            _attr(ca, "weakness"),
            _attr(ca, "resistance"),
            _attr(ca, "retreatCost"),
            _attr(ca, "description"),
            _attr(ca, "flavorText"),
            _attr(ca, "detailNote"),
            _attr(ca, "releaseDate"),
            product_url,
            IMAGE_URL.format(pid=pid),
            image_path,
            _to_float(product.get("marketPrice")),
            _to_float(product.get("lowestPrice")),
            _to_float(product.get("lowestPriceWithShipping")),
            product.get("totalListings"),
            json.dumps(ca, ensure_ascii=False),
            json.dumps(product, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
            pid,
        ),
    )


def store_price_history(conn, product_id, history):
    if not history:
        return 0
    rows = history.get("result") or history.get("results") or []
    inserted = 0
    for series in rows:
        variant = series.get("variant") or series.get("printingType") or "Normal"
        condition = series.get("condition") or "Unknown"
        language = series.get("language") or "English"
        for b in series.get("buckets", []):
            date = b.get("bucketStartDate") or b.get("date")
            conn.execute(
                """
                INSERT OR IGNORE INTO price_history (
                    product_id, variant, condition, language, bucket_date, market_price,
                    quantity_sold, low_sale, low_sale_ship, high_sale, trans_count
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    product_id, variant, condition, language, date,
                    _to_float(b.get("marketPrice")),
                    _to_float(b.get("quantitySold")),
                    _to_float(b.get("lowSalePrice")),
                    _to_float(b.get("lowSalePriceWithShipping")),
                    _to_float(b.get("highSalePrice")),
                    _to_float(b.get("transactionCount")),
                ),
            )
            inserted += 1
    conn.execute(
        "UPDATE cards SET history_fetched=1, history_fetched_at=? WHERE product_id=?",
        (datetime.now(timezone.utc).isoformat(), product_id),
    )
    return inserted


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Scrape Pokemon cards from TCGplayer.")
    ap.add_argument("--db", default=DB_PATH, help="SQLite output path")
    ap.add_argument("--image-dir", default=IMAGE_DIR, help="image download folder")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help="minimum seconds between consecutive requests")
    ap.add_argument("--rpm", type=float, default=DEFAULT_RPM,
                    help="hard ceiling on requests per rolling minute")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N cards (testing)")
    ap.add_argument("--no-images", action="store_true", help="skip image download")
    ap.add_argument("--no-history", action="store_true", help="skip price history")
    ap.add_argument("--include-sealed", action="store_true",
                    help="also include sealed/other products "
                         "(default: individual cards only)")
    ap.add_argument("--resume", action="store_true",
                    help="skip cards whose history was already fetched")
    ap.add_argument("--refresh-days", type=float, default=None,
                    help="incremental refresh: skip an existing card's price-history "
                         "refetch if it was last fetched within this many days "
                         "(new cards are always fetched)")
    ap.add_argument("--min-price", type=float, default=None,
                    help="incremental refresh: skip price-history refetch for existing "
                         "cards whose market_price is below this value "
                         "(new cards are always fetched)")
    ap.add_argument("--new-only", action="store_true",
                    help="scan only sets whose product count changed since the last "
                         "complete scan (new cards + their images); the fast nightly "
                         "mode now that all pricing comes from PriceCharting")
    args = ap.parse_args()

    global RATE_LIMITER
    RATE_LIMITER = RateLimiter(rpm=args.rpm, min_interval=args.delay)
    print(f"Rate limit: <= {args.rpm:g} requests/min, "
          f">= {args.delay:g}s between requests.")

    session = make_session()
    conn = sqlite3.connect(args.db)
    init_db(conn)

    singles_only = not args.include_sealed
    if args.new_only:
        print("Phase 1/2: enumerating changed sets only (--new-only)...")
        products = list(iter_new_products(
            session, args.delay, conn, limit=args.limit, singles_only=singles_only))
    else:
        print("Phase 1/2: enumerating catalog...")
        products = list(iter_all_products(
            session, args.delay, limit=args.limit, singles_only=singles_only))

    if singles_only:
        # Server-side productTypeName=Cards filter should already exclude sealed;
        # this is a belt-and-suspenders pass in case the API ignores it.
        before = len(products)
        products = [p for p in products if is_single_card(p)]
        print(f"Filtered to {len(products)} individual cards "
              f"(dropped {before - len(products)} sealed/other products).")

    print(f"Collected {len(products)} products. Writing card info...")
    for p in products:
        upsert_card(conn, p, None)
    conn.commit()

    print("Phase 2/2: images + price history...")
    done = 0
    skipped = 0
    for p in products:
        pid = p.get("productId")
        if pid is None:
            continue

        row = conn.execute(
            "SELECT history_fetched, history_fetched_at, market_price FROM cards WHERE product_id=?",
            (pid,),
        ).fetchone()
        hist_fetched = row[0] if row else 0
        last_fetched = row[1] if row else None
        market_price = row[2] if row else None

        # Does this card need a price-history (re)fetch?
        #  - new cards (never fetched) always do
        #  - --resume skips already-fetched cards (unless --refresh-days is used)
        #  - --refresh-days N re-fetches existing cards whose history is older than N days
        #  - --min-price / --no-history suppress it
        need_history = not args.no_history
        if need_history and args.resume and args.refresh_days is None and hist_fetched == 1:
            need_history = False
        if need_history and args.refresh_days is not None and last_fetched:
            try:
                age_days = (datetime.now(timezone.utc)
                            - datetime.fromisoformat(last_fetched)).total_seconds() / 86400.0
                if age_days < args.refresh_days:
                    need_history = False
            except ValueError:
                pass
        if (args.min_price is not None and market_price is not None
                and market_price < args.min_price):
            need_history = False

        # Does this card need its image? Only if it's missing on disk (new cards,
        # or ones that failed before). Existing images are never re-downloaded.
        need_image = False
        if not args.no_images:
            img_file = os.path.join(args.image_dir, f"{pid}.jpg")
            need_image = not (os.path.exists(img_file) and os.path.getsize(img_file) > 0)

        if not need_history and not need_image:
            skipped += 1
            continue

        if need_image:
            img_path = download_image(session, pid, args.delay, args.image_dir)
            if img_path:
                conn.execute(
                    "UPDATE cards SET image_path=? WHERE product_id=?", (img_path, pid))

        n = 0
        if need_history:
            hist = fetch_price_history(session, pid, args.delay)
            n = store_price_history(conn, pid, hist)

        conn.commit()
        done += 1
        if done % 25 == 0:
            print(f"  processed {done}/{len(products)} "
                  f"(last: {p.get('productName')}, {n} price points)")

    conn.commit()

    # Summary
    cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    points = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    conn.close()
    print(f"\nDone. {cards} cards, {points} price-history points "
          f"({skipped} cards skipped this run) -> {args.db}")
    if not args.no_images:
        print(f"Images in: {args.image_dir}/")


if __name__ == "__main__":
    main()
