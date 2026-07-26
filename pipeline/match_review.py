#!/usr/bin/env python3
"""Local GUI to confirm each card's PriceCharting match by eye.

The 25x sanity gate catches wild mismatches, but a crossed listing at a
similar price (the Jinbe P-030 case) only a human can spot. This serves a
review queue on http://localhost:8766: every matched card not yet reviewed,
newest first, with the TCGplayer identity (name/set/number/art) beside the
PriceCharting listing it points at (pc_name/console/prices + a link to the
live PC page). Per card:

  Confirm       -> recorded in ml_data/pc_match_reviewed.csv; never asked again.
  Wrong match   -> paste the correct pc_id from the PC page URL; appends a
                   pinned override to pc_match_overrides.csv (which pc-match
                   already honors), purges the wrongly-attributed graded
                   history, and clears graded_crawled so the nightly recrawls
                   the right page.

First run: click "Baseline" to grandfather every existing match so the queue
only ever holds cards matched AFTER today. A reviewed card whose pc_id later
changes under it resurfaces automatically with a MATCH CHANGED badge.

Stdlib only; binds localhost. Run:  python3 pipeline/match_review.py
"""
import csv
import json
import os
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import GAMES, db_path

PORT = 8766
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
ML_DATA = os.environ.get("MATCH_REVIEW_STATE", os.path.join(BASE, "ml_data"))
REVIEWED_CSV = os.path.join(ML_DATA, "pc_match_reviewed.csv")
OVERRIDES_CSV = os.path.join(ML_DATA, "pc_match_overrides.csv")
PAGE_CAP = 200   # rows served per request; the header shows the true totals

_lock = threading.Lock()   # serialize CSV appends + purge writes


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_reviewed():
    """(game, product_id) -> last reviewed pc_id."""
    out = {}
    if os.path.exists(REVIEWED_CSV):
        with open(REVIEWED_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(r["game"], int(r["product_id"]))] = int(r["pc_id"])
    return out


def append_reviewed(rows):
    new = not os.path.exists(REVIEWED_CSV)
    with open(REVIEWED_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["game", "product_id", "pc_id", "status", "reviewed_at"])
        w.writerows(rows)


def catalog_info(game, pids):
    """product_id -> (name, set, number, rarity, image_url, nm_price)."""
    if not pids:
        return {}
    conn = sqlite3.connect(db_path(game), timeout=30)
    marks = ",".join("?" * len(pids))
    rows = conn.execute(
        f"SELECT product_id, name, set_name, card_number, rarity, image_url, near_mint_price "
        f"FROM cards WHERE product_id IN ({marks})", list(pids)).fetchall()
    conn.close()
    return {r[0]: r[1:] for r in rows}


import re
# Event-style qualifiers in OUR card names that should normally correspond to
# a [bracketed] PriceCharting variant page rather than the plain base card.
EVENT_QUALIFIER = re.compile(
    r"\((Treasure Cup|Online Regional|Championship|Release Event|Winner|Finalist|Participa|Judge"
    r"|Event|Store|Tournament|Serial|Pre-?Release|Anniversary|Illustration|Staff|League|Prize"
    r"|Play Promo|Premier|Regional|National|Worlds|D23|Gen Con|Comic Con|Convention)", re.I)


def pending(game, reviewed):
    """Unreviewed (or drifted) matches for one game, newest product_id first.
    Returns (total_pending, capped list of row dicts)."""
    conn = sqlite3.connect(PC_DB, timeout=30)
    rows = conn.execute(
        "SELECT product_id, pc_id, pc_name, pc_console, ungraded, psa10, sales_volume "
        "FROM pricecharting WHERE game=? AND pc_id IS NOT NULL "
        "ORDER BY product_id DESC", (game,)).fetchall()
    conn.close()
    todo = []
    for pid, pc_id, pc_name, pc_console, ungraded, psa10, vol in rows:
        seen = reviewed.get((game, pid))
        if seen == pc_id:
            continue
        todo.append({"product_id": pid, "pc_id": pc_id, "pc_name": pc_name,
                     "pc_console": pc_console, "ungraded": ungraded,
                     "psa10": psa10, "volume": vol,
                     "drifted": seen is not None})
    total = len(todo)
    todo = todo[:PAGE_CAP]
    info = catalog_info(game, [t["product_id"] for t in todo])
    for t in todo:
        name, set_name, number, rarity, img, nm = info.get(
            t["product_id"], (None,) * 6)
        t.update({"name": name, "set": set_name, "number": number,
                  "rarity": rarity, "image": img, "nm_price": nm,
                  # The Koby class: OUR name carries an event qualifier but the
                  # PC page has no [bracket] variant — often PC hung the promo's
                  # tcg-id on the plain base-card page.
                  "event_mismatch": bool(name and EVENT_QUALIFIER.search(name)
                                         and "[" not in (t["pc_name"] or ""))})
    return total, todo


def orphans(game, limit=50):
    """Cards with price history but NO current row in the match table — the
    forecast review gate holds these out of the model, and they never appear
    in the normal queue (which walks the match table). Surfaced so they're
    visible rather than silently forecast-less; they re-enter the queue on
    their own if a match reappears."""
    conn = sqlite3.connect(PC_DB, timeout=30)
    pids = [r[0] for r in conn.execute(
        "SELECT DISTINCT h.product_id FROM price_history_unified h "
        "LEFT JOIN pricecharting p ON p.game = h.game AND p.product_id = h.product_id "
        "WHERE h.game=? AND p.product_id IS NULL ORDER BY h.product_id DESC LIMIT ?",
        (game, limit + 1))]
    conn.close()
    more = len(pids) > limit
    pids = pids[:limit]
    info = catalog_info(game, pids)
    return more, [{"product_id": p,
                   "name": (info.get(p) or (None,))[0],
                   "set": (info.get(p) or (None, None))[1]} for p in pids]


def purge_bad_history(game, product_id):
    """A wrong match means the crawled graded history belongs to some other
    card. Delete it and the crawled marker so the nightly re-fetches the
    correct page under the overridden pc_id (the Jinbe-fix procedure)."""
    conn = sqlite3.connect(PC_DB, timeout=60)
    h = conn.execute("DELETE FROM graded_price_history WHERE game=? AND product_id=?",
                     (game, product_id)).rowcount
    try:
        c = conn.execute("DELETE FROM graded_crawled WHERE game=? AND product_id=?",
                         (game, product_id)).rowcount
    except sqlite3.OperationalError:
        c = 0
    conn.commit()
    conn.close()
    return h, c


def append_override(game, product_id, pc_id, note):
    new = not os.path.exists(OVERRIDES_CSV)
    with open(OVERRIDES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["game", "product_id", "pc_id", "note"])
        w.writerow([game, product_id, pc_id, note])


PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>PC match review</title><style>
  body { font: 14px -apple-system, sans-serif; background: #10141d; color: #e8ecf4;
         margin: 0; padding: 20px; }
  h1 { font-size: 18px; } a { color: #7ab7ff; }
  .top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
  .tab { padding: 6px 12px; border-radius: 6px; background: #1b2231; cursor: pointer;
         border: 1px solid #2e3a52; }
  .tab.active { background: #2c5c9c; }
  .card { display: flex; gap: 16px; background: #171d2a; border: 1px solid #2e3a52;
          border-radius: 8px; padding: 14px; margin-bottom: 10px; align-items: center; }
  .card img { width: 70px; border-radius: 4px; background: #0c0f16; }
  .half { flex: 1; min-width: 0; }
  .nm { font-weight: 700; }
  .sub, .price { color: #8b96ad; font-size: 12.5px; }
  .badge { background: #a33; color: #fff; border-radius: 4px; font-size: 11px;
           padding: 2px 6px; margin-left: 8px; }
  button { background: #2c5c9c; color: #fff; border: 0; border-radius: 6px;
           padding: 8px 14px; cursor: pointer; font-size: 13px; }
  button.warn { background: #7c3a3a; }
  button.ghost { background: #1b2231; border: 1px solid #2e3a52; }
  .fix { display: none; gap: 6px; margin-top: 8px; }
  .fix input { background: #0c0f16; color: #e8ecf4; border: 1px solid #2e3a52;
               border-radius: 6px; padding: 7px; }
  .fix input[name=pc_id] { width: 110px; } .fix input[name=note] { flex: 1; }
  #status { margin-left: auto; color: #8b96ad; }
</style></head><body>
<h1>PriceCharting match review</h1>
<div class="top">
  <span id="tabs"></span>
  <button class="ghost" onclick="baseline()" title="Mark every currently-matched card as reviewed; only cards matched after today will queue up.">Baseline all existing</button>
  <span id="status"></span>
</div>
<div id="list"></div>
<div id="orphans"></div>
<script>
let game = null, data = null;
async function load(g) {
  game = g;
  const r = await fetch('/pending' + (g ? '?game=' + g : ''));
  data = await r.json();
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = Object.entries(data.counts).map(([k, n]) =>
    `<span class="tab ${k===data.game?'active':''}" onclick="load('${k}')">${k} (${n})</span>`).join(' ');
  document.getElementById('status').textContent =
    data.total > data.rows.length ? `showing ${data.rows.length} of ${data.total}` : `${data.total} pending`;
  const orph = !data.orphans?.length ? '' :
    `<h3 style="color:#8b96ad;font-size:13px;margin-top:24px">Orphaned history — ${data.orphans.length}${data.orphans_more ? '+' : ''} card(s) with price history but no current PriceCharting match (held out of the model; they re-queue if a match reappears)</h3>` +
    data.orphans.map(o => `<div class="card" style="opacity:.65"><div class="half">
      <div class="nm"><a target="_blank" href="https://www.tcgplayer.com/product/${o.product_id}">${esc(o.name || '(id ' + o.product_id + ')')}</a></div>
      <div class="sub">${esc(o.set || '')} &middot; pid ${o.product_id}</div>
    </div></div>`).join('');
  document.getElementById('orphans').innerHTML = orph;
  document.getElementById('list').innerHTML = data.rows.map(row => `
    <div class="card" id="c${row.product_id}">
      <img src="${row.image || ''}" loading="lazy" onerror="this.style.visibility='hidden'">
      <div class="half">
        <div class="nm"><a target="_blank" title="Open on TCGplayer (the listing this card came from)"
            href="https://www.tcgplayer.com/product/${row.product_id}">${esc(row.name || '(id ' + row.product_id + ')')}</a>
          ${row.drifted ? '<span class="badge">MATCH CHANGED</span>' : ''}
          ${row.event_mismatch ? '<span class="badge" style="background:#a80">EVENT CARD → PLAIN PAGE?</span>' : ''}</div>
        <div class="sub">${esc(row.set || '')} &middot; ${esc(row.number || '')} &middot; ${esc(row.rarity || '')}</div>
        <div class="price">our NM: ${money(row.nm_price)}</div>
      </div>
      <div class="half">
        <div class="nm"><a target="_blank" href="https://www.pricecharting.com/game/${row.pc_id}">${esc(row.pc_name || 'pc ' + row.pc_id)}</a></div>
        <div class="sub">${esc(row.pc_console || '')} &middot; pc_id ${row.pc_id}</div>
        <div class="price">PC raw: ${money(row.ungraded)} &middot; PSA10: ${money(row.psa10)} &middot; vol ${row.volume ?? '—'}</div>
      </div>
      <div>
        <button onclick="confirmOk(${row.product_id}, ${row.pc_id})">Confirm</button>
        <button class="warn" onclick="toggleFix(${row.product_id})">Wrong…</button>
        <div class="fix" id="f${row.product_id}">
          <input name="pc_id" placeholder="correct pc_id">
          <input name="note" placeholder="note (what/why)">
          <button class="warn" onclick="override(${row.product_id})">Save fix</button>
          <button class="warn" title="PriceCharting has no correct page for this card — unmatch it and purge its absorbed history; it stays unpriced rather than mispriced"
            onclick="exclude(${row.product_id})">No PC page — exclude</button>
        </div>
      </div>
    </div>`).join('') || '<p>Queue empty — every match is reviewed. 🎉</p>';
}
const esc = s => (s ?? '').toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money = v => v == null ? '—' : '$' + Number(v).toFixed(2);
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify(body)});
  if (!r.ok) { alert(await r.text()); throw 0; }
  return r.json();
}
async function confirmOk(pid, pcId) {
  await post('/confirm', {game, product_id: pid, pc_id: pcId});
  document.getElementById('c' + pid).remove();
}
function toggleFix(pid) {
  const f = document.getElementById('f' + pid);
  f.style.display = f.style.display === 'flex' ? 'none' : 'flex';
}
async function override(pid) {
  const f = document.getElementById('f' + pid);
  const pcId = f.querySelector('[name=pc_id]').value.trim();
  const note = f.querySelector('[name=note]').value.trim();
  if (!/^\\d+$/.test(pcId)) { alert('pc_id must be the number from the PriceCharting page URL'); return; }
  const res = await post('/override', {game, product_id: pid, pc_id: +pcId, note});
  alert(`Override saved. Purged ${res.purged_history} old history rows; the nightly will recrawl.`);
  document.getElementById('c' + pid).remove();
}
async function exclude(pid) {
  if (!confirm('Unmatch this card? Its absorbed price history is purged and it stays UNPRICED until PriceCharting grows a correct page (re-pin it here then).')) return;
  const note = document.getElementById('f' + pid).querySelector('[name=note]').value.trim();
  const res = await post('/override', {game, product_id: pid, pc_id: 0,
                                       note: note || 'excluded — no correct PC page'});
  alert(`Excluded. Purged ${res.purged_history} wrong history rows.`);
  document.getElementById('c' + pid).remove();
}
async function baseline() {
  if (!confirm('Grandfather ALL currently-matched cards as reviewed? Only cards matched after today will queue.')) return;
  const res = await post('/baseline', {});
  alert(res.marked.toLocaleString() + ' matches baselined.');
  load(game);
}
load(null);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/pending"):
            reviewed = load_reviewed()
            counts, per_game = {}, {}
            for g in GAMES:
                total, rows = pending(g, reviewed)
                counts[g] = total
                per_game[g] = rows
            want = None
            if "game=" in self.path:
                want = self.path.split("game=")[1].split("&")[0]
            if want not in GAMES:
                # default to the game with the most pending work
                want = max(counts, key=counts.get)
            more, orph = orphans(want)
            self._json({"game": want, "counts": counts,
                        "total": counts[want], "rows": per_game[want],
                        "orphans": orph, "orphans_more": more})
            return
        self.send_error(404)

    def do_POST(self):
        # CSRF insurance: a malicious webpage could try cross-origin POSTs at
        # localhost:8766 (browser private-network blocking isn't universal).
        # Browsers always send Origin on cross-origin fetch — reject any value
        # that isn't this server. curl/scripts send none and pass.
        origin = self.headers.get("Origin")
        allowed = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
        if (origin and origin not in allowed) or \
                self.headers.get("Host", "").split(":")[0] not in ("localhost", "127.0.0.1"):
            self.send_error(403, "cross-origin request rejected")
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "bad json")
            return
        with _lock:
            if self.path == "/confirm":
                append_reviewed([[body["game"], body["product_id"], body["pc_id"],
                                  "confirmed", now_utc()]])
                self._json({"ok": True})
            elif self.path == "/override":
                game, pid, pc_id = body["game"], body["product_id"], body["pc_id"]
                append_override(game, pid, pc_id, body.get("note", ""))
                h, c = purge_bad_history(game, pid)
                append_reviewed([[game, pid, pc_id, "override", now_utc()]])
                self._json({"ok": True, "purged_history": h, "cleared_crawled": c})
            elif self.path == "/baseline":
                reviewed = load_reviewed()
                conn = sqlite3.connect(PC_DB, timeout=30)
                rows = []
                stamp = now_utc()
                for g in GAMES:
                    for pid, pc_id in conn.execute(
                            "SELECT product_id, pc_id FROM pricecharting "
                            "WHERE game=? AND pc_id IS NOT NULL", (g,)):
                        if reviewed.get((g, pid)) != pc_id:
                            rows.append([g, pid, pc_id, "baseline", stamp])
                conn.close()
                append_reviewed(rows)
                self._json({"ok": True, "marked": len(rows)})
            else:
                self.send_error(404)

    def log_message(self, *a):   # quiet
        pass


def any_pending():
    reviewed = load_reviewed()
    return any(pending(g, reviewed)[0] for g in GAMES)


def main():
    url = f"http://localhost:{PORT}"
    # --if-pending: the nightly's hook. Exit quietly when the queue is empty
    # so a browser tab only ever appears when there is real work to review.
    if "--if-pending" in sys.argv and not any_pending():
        print("match review: queue empty — not opening")
        return
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # already serving (user left it running) — just surface the tab
        print(f"match review already running -> {url}")
        if "--no-open" not in sys.argv:
            webbrowser.open(url)
        return
    print(f"match review -> {url}  (Ctrl-C to stop)")
    if "--no-open" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
