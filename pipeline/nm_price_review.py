#!/usr/bin/env python3
"""Local GUI to adjudicate TCGplayer-vs-PriceCharting price divergences by eye.

sweep_nm_vs_pc.py flags high-value cards whose TCGplayer NM price sits >= 2x
above PriceCharting's sales price. The two failure classes are mirror images
only a human can separate reliably:

  TCGplayer right  -> PC carries junk for a card it barely tracks (serialized,
                      UTR, tournament promos). No data action needed (TCG
                      already serves); recorded so it never resurfaces.
  PC right         -> a lone bogus TCGplayer listing (the Umbreon H30 case).
                      Full repair, applied immediately on the Mac: card added
                      to tcg_nm_blocklist.csv (crawl skip + PC-owned ungraded),
                      its NM rows deleted, unified ungraded rebuilt from PC,
                      catalog price reset to PC's latest, and the card queued
                      in forecast_purge_queue.csv (the trainer's scorecard
                      purges + regenerates its forecasts on the next run).
                      Prod SQL accumulates in-session — the "Apply to prod"
                      button ships it (deletes + rebuilt rows + catalog price
                      + forecast purge) and restarts the API once.

Decisions memo to ml_data/nm_pc_reviewed.csv; the sweep re-flags a reviewed
card only if its TCGplayer price later drifts >20% from the reviewed value.

Stdlib only; binds localhost. Run:  python3 pipeline/nm_price_review.py
"""
import csv
import html
import json
import os
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import DATA_DIR as BASE
from games import db_path

PORT = 8767
PC_DB = os.path.join(BASE, "..", "tcg-predictor", "dotnet", "API", "Data", "cards", "pricecharting.db")
ML = os.path.join(BASE, "ml_data")
SWEEP_CSV = os.path.join(ML, "nm_pc_divergence_review.csv")
REVIEWED_CSV = os.path.join(ML, "nm_pc_reviewed.csv")
BLOCKLIST_CSV = os.path.join(ML, "tcg_nm_blocklist.csv")
PURGE_QUEUE_CSV = os.path.join(ML, "forecast_purge_queue.csv")
REVIEWED_MATCH_CSV = os.path.join(ML, "pc_match_reviewed.csv")

PROD_IP = "35.168.177.31"
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/tcg-predictor.pem"),
       "-o", "StrictHostKeyChecking=no", f"ubuntu@{PROD_IP}"]

_lock = threading.Lock()
_prod_sql = []          # accumulated prod statements (applied on demand)
_prod_restart = False


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def reviewed():
    out = {}
    if os.path.exists(REVIEWED_CSV):
        with open(REVIEWED_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(r["game"], int(r["product_id"]))] = r
    return out


def confirmed_matches():
    """(game, pid) set whose PC match the user has confirmed at the current pc_id."""
    ok = set()
    if not os.path.exists(REVIEWED_MATCH_CSV):
        return ok
    conn = sqlite3.connect(PC_DB, timeout=30)
    cur = {(g, p): pc for g, p, pc in conn.execute(
        "SELECT game, product_id, pc_id FROM pricecharting WHERE pc_id IS NOT NULL")}
    with open(REVIEWED_MATCH_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["game"], int(r["product_id"]))
            if cur.get(k) == int(r["pc_id"] or 0):
                ok.add(k)
    return ok


def card_info(game, pids):
    conn = sqlite3.connect(db_path(game), timeout=30)
    q = ",".join("?" * len(pids))
    return {pid: (n, s, num, img, pr, bp) for pid, n, s, num, img, pr, bp in conn.execute(
        f"SELECT product_id, name, set_name, card_number, image_url, printings, base_printing "
        f"FROM cards WHERE product_id IN ({q})", list(pids))}


def pinned_variants():
    """(game, pid) -> pinned variant name (tcg_printing_pins.csv): these cards'
    NM series is one specific printing — a price divergence may be a PRINTING
    mismatch against PC's base page, not a bogus listing."""
    path = os.path.join(ML, "tcg_printing_pins.csv")
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("product_id"):
                    out[(r["game"], int(r["product_id"]))] = r.get("variant") or r.get("printing") or "?"
    return out


def pc_ids(game, pids):
    conn = sqlite3.connect(PC_DB, timeout=30)
    q = ",".join("?" * len(pids))
    return dict(conn.execute(
        f"SELECT product_id, pc_id FROM pricecharting WHERE game=? AND product_id IN ({q})",
        [game] + list(pids)))


def queue_rows():
    if not os.path.exists(SWEEP_CSV):
        return []
    done = reviewed()
    conf = confirmed_matches()
    rows = []
    with open(SWEEP_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["game"], int(r["product_id"]))
            memo = done.get(k)
            if memo:
                try:  # re-surface only on >20% TCG price drift since review
                    if abs(float(r["tcg_nm"]) / float(memo["tcg_nm"]) - 1) <= 0.20:
                        continue
                except (ValueError, ZeroDivisionError):
                    continue
            rows.append({**r, "match_confirmed": k in conf})
    rows.sort(key=lambda r: (not r["match_confirmed"], -float(r["tcg_nm"])))
    by_game = {}
    for r in rows:
        by_game.setdefault(r["game"], []).append(int(r["product_id"]))
    infos = {g: card_info(g, set(p)) for g, p in by_game.items()}
    pcs = {g: pc_ids(g, set(p)) for g, p in by_game.items()}
    pins = pinned_variants()
    out = []
    for r in rows[:150]:
        g, pid = r["game"], int(r["product_id"])
        name, set_name, num, img, printings, base_pr = infos[g].get(pid, (None,) * 6)
        try:
            printings = json.loads(printings) if printings else []
        except (TypeError, ValueError):
            printings = []
        out.append({
            "game": g, "pid": pid, "name": name, "set": set_name, "number": num,
            "image": img,
            "printings": printings, "base_printing": base_pr,
            "pinned": pins.get((g, pid)),
            # cards.product_url is unreliable (some games store a bare name);
            # TCGplayer resolves by product id alone.
            "tcg_url": f"https://www.tcgplayer.com/product/{pid}",
            "pc_url": f"https://www.pricecharting.com/game/{pcs[g].get(pid)}" if pcs[g].get(pid) else None,
            "site_url": f"https://cardstock.guide/catalog/{g}/{pid}",
            "tcg": float(r["tcg_nm"]), "tcg_date": r["tcg_date"],
            "pc": float(r["pc_ungraded"]), "pc_date": r["pc_date"],
            "psa10": r["pc_psa10"], "ratio": r["ratio"],
            "hint": r["class_hint"], "match_confirmed": r["match_confirmed"],
        })
    return out, len(rows)


def repair_pc_right(game, pid, reason):
    """Mac-side repair + prod SQL accumulation for a bogus-TCG-listing card."""
    global _prod_restart
    pc = sqlite3.connect(PC_DB, timeout=60)
    latest = pc.execute(
        "SELECT price FROM graded_price_history WHERE game=? AND product_id=? "
        "AND grade='ungraded' AND printing='' AND price>0 ORDER BY date DESC LIMIT 1",
        (game, pid)).fetchone()
    if not latest:
        return "no PC ungraded data — not repaired (review manually)"
    price = latest[0]
    append_csv(BLOCKLIST_CSV, ["game", "product_id", "reason"],
               [game, pid, f"{reason} (review {now_utc()[:10]})"])
    append_csv(PURGE_QUEUE_CSV, ["game", "product_id", "queued_at"],
               [game, pid, now_utc()])
    pc.execute("DELETE FROM tcg_nm_history WHERE game=? AND product_id=?", (game, pid))
    pc.execute("DELETE FROM price_history_unified WHERE game=? AND product_id=? "
               "AND grade='ungraded' AND printing=''", (game, pid))
    n = pc.execute(
        "INSERT OR REPLACE INTO price_history_unified "
        "(game, product_id, printing, grade, date, price, source) "
        "SELECT game, product_id, '', 'ungraded', date, price, 'pricecharting' "
        "FROM graded_price_history WHERE game=? AND product_id=? AND grade='ungraded' "
        "AND printing='' AND price>0", (game, pid)).rowcount
    pc.commit()
    cards = sqlite3.connect(db_path(game), timeout=60)
    cards.execute("UPDATE cards SET near_mint_price=? WHERE product_id=?", (price, pid))
    cards.commit()
    rows = pc.execute(
        "SELECT date, price FROM graded_price_history WHERE game=? AND product_id=? "
        "AND grade='ungraded' AND printing='' AND price>0", (game, pid)).fetchall()
    stmts = [f"DELETE FROM tcg_nm_history WHERE game='{game}' AND product_id={pid};",
             f"DELETE FROM price_history_unified WHERE game='{game}' AND product_id={pid}"
             " AND grade='ungraded' AND printing='';"]
    stmts += [f"INSERT OR REPLACE INTO price_history_unified VALUES "
              f"('{game}',{pid},'','ungraded','{d}',{p},'pricecharting');" for d, p in rows]
    _prod_sql.append(("pricecharting", "\n".join(stmts)))
    _prod_sql.append(("cards", f"UPDATE cards SET near_mint_price={price} WHERE product_id={pid};|{game}"))
    _prod_sql.append(("predictions",
                      f"DELETE FROM forecasts WHERE game='{game}' AND product_id={pid};"
                      f"DELETE FROM forecast_archive WHERE game='{game}' AND product_id={pid};"))
    _prod_restart = True
    return f"repaired: PC-owned, {n} unified rows, catalog ${price:,.2f}, forecasts queued"


def apply_prod():
    global _prod_sql, _prod_restart
    if not _prod_sql:
        return "nothing pending"
    dbs = {"pricecharting": "/srv/tcg/data/cards/pricecharting.db",
           "predictions": "/srv/tcg/data/cards/predictions.db"}
    n = len(_prod_sql)
    for target, sql in _prod_sql:
        if target == "cards":
            stmt, game = sql.split("|")
            cmd = f"sqlite3 /srv/tcg/data/{game}_cards.db \"{stmt}\""
        else:
            cmd = f"sqlite3 {dbs[target]} <<'SQL'\n{sql}\nSQL"
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return f"FAILED: {r.stderr[:300]}"
    _prod_sql = []
    if _prod_restart:
        subprocess.run(SSH + ["sudo systemctl restart tcg-api && sleep 2 && systemctl is-active tcg-api"],
                       capture_output=True, text=True, timeout=60)
        _prod_restart = False
    return f"applied {n} statement group(s) + API restarted"


PAGE = """<!doctype html><meta charset="utf-8"><title>NM price review</title>
<style>
 body { font: 14px/1.5 -apple-system, sans-serif; background:#0e1117; color:#dde3ee; margin:20px; }
 .head { display:flex; gap:16px; align-items:center; margin-bottom:14px; }
 button { background:#1c2536; color:#dde3ee; border:1px solid #33415c; border-radius:6px; padding:6px 12px; cursor:pointer; }
 button:hover { background:#26324a; }
 .card { display:flex; gap:14px; background:#151b26; border:1px solid #232d40; border-radius:10px; padding:12px; margin-bottom:10px; align-items:center; }
 .card img { width:74px; border-radius:5px; background:#0c0f16; }
 .grow { flex:1; }
 .prices { display:flex; gap:22px; margin-top:4px; }
 .prices b { font-size:16px; }
 .tag { font-size:11px; padding:2px 7px; border-radius:9px; background:#233047; margin-left:8px; }
 .ok { background:#1d3a2a; }
 a { color:#7fb3ff; text-decoration:none; margin-right:10px; }
 .done { opacity:.45; }
 .msg { color:#9fd0a0; font-size:12px; }
</style>
<div class="head">
  <h2 style="margin:0">TCG vs PC price review</h2>
  <span id="count"></span>
  <button onclick="applyProd()">Apply pending to prod</button>
  <span id="prodmsg" class="msg"></span>
</div>
<div id="list">loading…</div>
<script>
async function load() {
  const d = await (await fetch('/queue')).json();
  document.getElementById('count').textContent = d.total + ' pending · showing ' + d.rows.length;
  document.getElementById('list').innerHTML = d.rows.map(r => `
    <div class="card" id="c-${r.game}-${r.pid}">
      <img src="${r.image || ''}" loading="lazy" onerror="this.style.visibility='hidden'">
      <div class="grow">
        <b>${r.name || r.pid}</b> <span style="color:#8b96ad">${r.set || ''} ${r.number || ''}</span>
        ${r.match_confirmed ? '<span class="tag ok">match confirmed</span>' : '<span class="tag">match unreviewed</span>'}
        <span class="tag">${r.hint}</span>
        ${r.pinned ? `<span class="tag" style="background:#4a3320">series pinned: ${r.pinned} — divergence may be a printing mismatch</span>`
          : r.printings && r.printings.length
            ? `<span class="tag" style="background:#2c3a55">reviewing: ${r.base_printing || 'base'} · all printings: ${r.printings.join(', ')}</span>`
            : ''}
        <div class="prices">
          <span>TCGplayer <b>$${r.tcg.toLocaleString()}</b> <small>(${r.tcg_date})</small></span>
          <span>PC sales <b>$${r.pc.toLocaleString()}</b> <small>(${r.pc_date})</small></span>
          <span>PC psa10 <b>${r.psa10 ? '$' + (+r.psa10).toLocaleString() : '—'}</b></span>
          <span>ratio <b>${r.ratio}x</b></span>
        </div>
        <div>
          <a href="${r.tcg_url || '#'}" target="_blank">TCGplayer</a>
          <a href="${r.pc_url || '#'}" target="_blank">PriceCharting</a>
          <a href="${r.site_url}" target="_blank">cardstock</a>
          <button style="padding:2px 10px; font-size:12px"
            onclick='openAll(${JSON.stringify([r.tcg_url, r.pc_url, r.site_url])})'>
            Open all 3</button>
        </div>
      </div>
      <div style="display:flex; flex-direction:column; gap:6px">
        <button onclick="decide('${r.game}',${r.pid},'tcg_right')">TCGplayer is right</button>
        <button onclick="decide('${r.game}',${r.pid},'pc_right')">PC is right — repair</button>
        <button onclick="decide('${r.game}',${r.pid},'skip')">Skip for now</button>
      </div>
    </div>`).join('');
}
async function decide(game, pid, decision) {
  const el = document.getElementById('c-' + game + '-' + pid);
  el.classList.add('done');
  const r = await (await fetch('/decide', { method:'POST',
    body: JSON.stringify({game, pid, decision}) })).json();
  el.querySelector('.grow').insertAdjacentHTML('beforeend',
    '<div class="msg">' + r.msg + '</div>');
}
async function applyProd() {
  document.getElementById('prodmsg').textContent = 'applying…';
  const r = await (await fetch('/apply_prod', {method:'POST'})).json();
  document.getElementById('prodmsg').textContent = r.msg;
}
async function openAll(urls) {
  await fetch('/open', { method:'POST', body: JSON.stringify({urls: urls.filter(Boolean)}) });
}
load();
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/queue":
            with _lock:
                rows, total = queue_rows()
            self._json({"rows": rows, "total": total})
            return
        b = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/apply_prod":
            with _lock:
                self._json({"msg": apply_prod()})
            return
        if self.path == "/open":
            # Server-side open: dodges popup blockers (one click, three tabs).
            allowed = ("https://www.tcgplayer.com/", "https://www.pricecharting.com/",
                       "https://cardstock.guide/", "https://tcgplayer.com/",
                       "https://prices.pokemontcg.")
            opened = 0
            for u in body.get("urls", [])[:4]:
                if isinstance(u, str) and u.startswith(allowed):
                    webbrowser.open_new_tab(u)
                    opened += 1
            self._json({"msg": f"opened {opened} tab(s)"})
            return
        game, pid, decision = body["game"], int(body["pid"]), body["decision"]
        with _lock:
            if decision == "skip":
                self._json({"msg": "skipped"})
                return
            # find the sweep row for the memo's price snapshot
            tcg = pcp = ""
            with open(SWEEP_CSV, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["game"] == game and int(r["product_id"]) == pid:
                        tcg, pcp = r["tcg_nm"], r["pc_ungraded"]
                        break
            msg = "recorded: TCGplayer stands (PC junk ignored)"
            if decision == "pc_right":
                msg = repair_pc_right(game, pid,
                                      f"review: bogus TCG listing ${tcg} vs PC ${pcp}")
            append_csv(REVIEWED_CSV,
                       ["game", "product_id", "decision", "tcg_nm", "pc_ungraded", "reviewed_at"],
                       [game, pid, decision, tcg, pcp, now_utc()])
            self._json({"msg": msg})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"NM price review -> http://localhost:{PORT}  (Ctrl-C to stop; "
          f"remember 'Apply pending to prod' before quitting)")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()
