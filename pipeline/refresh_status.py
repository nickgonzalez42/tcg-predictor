#!/usr/bin/env python3
"""Local web dashboard for the daily price + model refresh.

Parses the newest ~/Library/Logs/tcg-predictor/refresh-*.log into a live step
checklist + tail and serves it on http://localhost:8765. Open the page in a
browser (this script opens it for you) and close the tab anytime — the refresh
runs independently. Stdlib only; no dependencies.

    python3 pipeline/refresh_status.py          # start + open browser
    python3 pipeline/refresh_status.py --no-open # just start the server
"""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_GLOB = os.path.expanduser("~/Library/Logs/tcg-predictor/refresh-*.log")
# The TCGplayer NM crawl records its per-set progress here (see
# scrape_tcg_nm_prices.py); we read it directly for an exact live percentage.
PC_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "dotnet", "API", "Data", "cards", "pricecharting.db")
PORT = 8765
STEP_RE = re.compile(r"^=== ([\w-]+): .* ===")          # step start
DONE_RE = re.compile(r"^--- ([\w-]+) done in ([\d.]+) min")  # step finished
TOTAL_RE = re.compile(r"— (\d+) step\(s\)")
HEAD_RE = re.compile(r"^=== .*refresh.* — ([\d-]+ [\d:]+)")
TAIL_LINES = 45


def pipeline_alive():
    try:
        subprocess.check_output(
            ["pgrep", "-f", "run_daily_refresh|weekly_refresh.py|forecast_predict.py"])
        return True
    except subprocess.CalledProcessError:
        return False


def _nm_alive():
    """Is a TCGplayer NM crawl process actually running right now? Drives the
    panel's live/idle badge, so a long in-flight set (no completions yet) still
    reads as 'crawling' rather than 'idle'."""
    try:
        subprocess.check_output(["pgrep", "-f", "scrape_tcg_nm_prices"])
        return True
    except subprocess.CalledProcessError:
        return False


def newest_log():
    files = glob.glob(LOG_GLOB)
    return max(files, key=os.path.getmtime) if files else None


def parse():
    path = newest_log()
    if not path:
        return {"state": "idle", "steps": [], "tail": [], "total": None,
                "started": None, "log": None, "nm": _tcgnm_progress()}
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()

    steps, order, total, started = [], {}, None, None
    for ln in lines:
        if (m := HEAD_RE.match(ln)) and started is None:
            started = m.group(1)
        if m := TOTAL_RE.search(ln):
            total = int(m.group(1))
        if m := STEP_RE.match(ln):
            name = m.group(1)
            if name not in order:
                order[name] = len(steps)
                steps.append({"name": name, "status": "running", "dur": None})
        if (m := DONE_RE.match(ln)) and m.group(1) in order:
            s = steps[order[m.group(1)]]
            s["status"], s["dur"] = "done", float(m.group(2))

    alive = pipeline_alive()
    complete = any("refresh complete" in ln or "data live on" in ln for ln in lines)
    running = next((s for s in steps if s["status"] == "running"), None)
    if not alive and running:
        running["status"] = "stopped" if not complete else "done"

    state = ("running" if alive else
             "complete" if complete else
             "stopped" if steps else "idle")
    done_n = sum(1 for s in steps if s["status"] == "done")

    # The two long steps get their own sub-progress so the UI isn't a static
    # spinner. Forecast reads its counter from the log; the TCGplayer NM crawl
    # reads its authoritative per-set progress table for an exact % + live rate.
    # nm_prog is surfaced independently too (top-level "nm") so the crawl is
    # watchable even when run standalone, not only as a refresh step.
    nm_prog = _tcgnm_progress()
    frac_in_step = 0.0
    if running and running["name"] == "forecast":
        sub = _forecast_progress(lines)
        running["sub"] = sub
        if sub and sub["total"]:
            frac_in_step = sub["done"] / sub["total"]
    elif running and running["name"] == "tcg-nm-prices" and nm_prog:
        running["subnm"] = nm_prog
        if nm_prog["total_sets"]:
            frac_in_step = nm_prog["done_sets"] / nm_prog["total_sets"]

    # Overall percent: completed top-level steps plus the running step's own
    # fraction, over the total step count.
    pct = None
    if total:
        pct = round(100 * (done_n + frac_in_step) / total)
    return {"state": state, "steps": steps, "total": total, "done": done_n,
            "pct": pct, "started": started, "log": os.path.basename(path),
            "nm": nm_prog, "tail": lines[-TAIL_LINES:]}


def _tcgnm_progress():
    """Live progress for the TCGplayer Near Mint crawl, read straight from its
    per-set marker table — an exact overall percentage plus a recent-throughput
    rate and ETA. Returns None if the table isn't there yet or can't be read.

    Rate is measured over the most recent scanned sets (not since the start), so
    it reflects the crawl's CURRENT speed even after a slow patch or a resume."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        # Read-only + short timeout: the crawl is actively writing this DB.
        con = sqlite3.connect(f"file:{os.path.abspath(PC_DB)}?mode=ro",
                              uri=True, timeout=2)
    except sqlite3.OperationalError:
        return None
    try:
        plans = con.execute(
            "SELECT game, total_sets, total_products FROM tcg_nm_plan WHERE date=?",
            (today,)).fetchall()
        if not plans:
            return None
        total_by_game = {g: (t or 0) for g, t, _ in plans}
        total_sets = sum(total_by_game.values())
        total_products = sum(p or 0 for _, _, p in plans)
        scan = con.execute(
            "SELECT game, n_cards, scanned_at FROM tcg_nm_scan WHERE date=?",
            (today,)).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()

    done_sets = len(scan)
    priced = sum((n or 0) for _, n, _ in scan)   # scan row = (game, n_cards, scanned_at)

    # Recent throughput from the last ~40 set timestamps.
    stamped = sorted((datetime.fromisoformat(s), n or 0)
                     for _, n, s in scan if s)
    now = datetime.now(timezone.utc)
    rate_sets = rate_cards = eta_min = last_age_sec = None
    if stamped:
        last_age_sec = (now - stamped[-1][0]).total_seconds()
    # Rate = CURRENT speed: only sets finished in the last 8 minutes, so it
    # recalibrates quickly between fast (tiny sets) and slow (big multi-page
    # sets) stretches and ignores any idle gap before the crawl resumed.
    window = [(t, n) for t, n in stamped if (now - t).total_seconds() <= 480]
    if len(window) >= 2:
        span_min = (window[-1][0] - window[0][0]).total_seconds() / 60.0
        if span_min > 0:
            rate_sets = (len(window) - 1) / span_min
            rate_cards = sum(n for _, n in window[1:]) / span_min
            # Card-throughput ETA — stable across set-size swings. A set-based
            # ETA lurches when a few huge multi-page sets dominate the window
            # (e.g. Pokémon's big modern sets early in the crawl). Dividing the
            # remaining products by the priced-card rate slightly overestimates
            # (some remaining products carry no price), i.e. it errs long.
            if rate_cards > 0 and total_products:
                eta_min = max(0.0, (total_products - priced) / rate_cards)

    done_by_game = {}
    for g, _, _ in scan:
        done_by_game[g] = done_by_game.get(g, 0) + 1
    current = next((g for g, _, _ in plans
                    if done_by_game.get(g, 0) < total_by_game.get(g, 0)), None)

    return {"done_sets": done_sets, "total_sets": total_sets,
            "priced": priced, "total_products": total_products,
            "rate_sets": rate_sets, "rate_cards": rate_cards,
            "eta_min": eta_min, "game": current, "last_age_sec": last_age_sec,
            "running": _nm_alive(),
            "complete": total_sets > 0 and done_sets >= total_sets}


def _forecast_progress(lines):
    """{done, total, current} for the forecast step, or None."""
    seg = None
    cur = None
    fidx = next((i for i, l in enumerate(lines) if l.startswith("=== forecast:")), 0)
    fc_lines = lines[fidx:]
    for ln in reversed(fc_lines):
        if seg is None and (m := re.search(r"\[forecast\] (\d+)/(\d+) segments", ln)):
            seg = (int(m.group(1)), int(m.group(2)))
        if cur is None and (m := re.match(r"^\[([a-z]+)/([a-z0-9]+)[\]/]", ln)):
            cur = f"{m.group(1)}/{m.group(2)}"
        if seg and cur:
            break
    if seg:
        return {"done": seg[0], "total": seg[1], "current": cur}
    # fallback: count completed (game, tier) 'rows' lines (no known total)
    done = sum(1 for l in fc_lines if re.match(r"^\[[a-z]+/[a-z0-9]+\] \d+ rows", l))
    if done or cur:
        return {"done": done, "total": None, "current": cur}
    return None


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Refresh · cardstock</title><style>
:root{color-scheme:dark}
body{margin:0;background:#0b0f19;color:#e8ecf4;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto}
.wrap{max-width:820px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:#8a93a6;font-size:12.5px;margin-bottom:18px}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.04em;vertical-align:2px;margin-left:8px}
.running{background:#12314a;color:#5cc3ff}.complete{background:#123a28;color:#3fd98a}
.stopped{background:#3a1520;color:#ff7a7a}.idle{background:#23293a;color:#8a93a6}
.bar{height:8px;background:#1a2133;border-radius:6px;overflow:hidden;margin:14px 0 22px}
.bar>i{display:block;height:100%;background:#3fd98a;transition:width .4s}
ul{list-style:none;padding:0;margin:0 0 24px}
li{display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid #151b28}
.ic{width:18px;text-align:center}.dur{margin-left:auto;color:#8a93a6;font-variant-numeric:tabular-nums;font-size:12px;text-align:right}
li.running .dur{color:#9fb4cc}
.done .ic{color:#3fd98a}.running .ic{color:#5cc3ff}.stopped .ic{color:#ff7a7a}
.name{text-transform:uppercase;letter-spacing:.03em;font-size:12.5px}
.running .name{color:#5cc3ff;font-weight:600}
pre{background:#070a12;border:1px solid #151b28;border-radius:8px;padding:12px 14px;
 font:11.5px/1.5 ui-monospace,Menlo,monospace;color:#c7cede;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.spin{display:inline-block;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
.foot{color:#5a6274;font-size:11.5px;margin-top:10px}
.nm{display:none;border:1px solid #21406a;background:#0e1a2c;border-radius:10px;padding:14px 16px;margin:0 0 22px}
.nm h2{margin:0 0 3px;font-size:13px;color:#5cc3ff;text-transform:uppercase;letter-spacing:.05em}
.nm .big{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums}
.nm .meta{color:#9fb4cc;font-size:12.5px;margin-top:2px;font-variant-numeric:tabular-nums}
.nm .bar{height:8px;margin:12px 0 0}.nm .bar>i{background:#5cc3ff}
.nm .live{color:#3fd98a}.nm .idle{color:#ffb454}
</style></head><body><div class=wrap>
<h1>Daily refresh <span id=badge class=badge></span></h1>
<div class=sub id=sub></div>
<div class=bar><i id=fill style=width:0></i></div>
<div class=nm id=nmcard>
  <h2>TCGplayer Near Mint crawl <span id=nmlive></span></h2>
  <div class=big id=nmpct>0%</div>
  <div class=meta id=nmmeta></div>
  <div class=bar><i id=nmfill style=width:0></i></div>
</div>
<ul id=steps></ul>
<pre id=tail></pre>
<div class=foot>Auto-updates every 2s · safe to close this tab anytime — the refresh keeps running.</div>
</div><script>
const IC={done:'✓',running:'◐',stopped:'✕',pending:'○'};
function fmtEta(m){ if(m<1)return '<1m'; if(m<60)return Math.round(m)+'m';
  const h=Math.floor(m/60), r=Math.round(m%60); return h+'h'+(r?' '+r+'m':''); }
async function tick(){
 let d; try{d=await (await fetch('/api/status')).json()}catch(e){return}
 const b=document.getElementById('badge');
 b.className='badge '+d.state; b.textContent=d.state;
 document.getElementById('sub').textContent =
   (d.log?d.log+' · ':'')+(d.started?'started '+d.started+' · ':'')+
   (d.total?(d.done+' of '+d.total+' steps done'):(d.done+' steps done'));
 document.getElementById('fill').style.width = (d.pct!=null?d.pct:0)+'%';
 // Dedicated live panel for the TCGplayer NM crawl (shown whenever a crawl is
 // in progress today, standalone or as a refresh step).
 const nc=document.getElementById('nmcard'), p=d.nm;
 if(p && p.total_sets && !p.complete){
   nc.style.display='block';
   const pct=Math.round(100*p.done_sets/p.total_sets);
   document.getElementById('nmpct').textContent=pct+'%';
   const live = p.running || (p.last_age_sec!=null && p.last_age_sec<120);
   const lv=document.getElementById('nmlive');
   lv.className = live?'live':'idle';
   lv.textContent = live?'● crawling':'○ idle';
   let m=[p.done_sets.toLocaleString()+' / '+p.total_sets.toLocaleString()+' sets',
          p.priced.toLocaleString()+' cards priced'];
   if(p.game) m.push('now: '+p.game);
   if(p.rate_cards) m.push(Math.round(p.rate_cards).toLocaleString()+' cards/min');
   if(p.rate_sets) m.push(p.rate_sets.toFixed(1)+' sets/min');
   if(p.eta_min!=null) m.push('ETA '+fmtEta(p.eta_min));
   document.getElementById('nmmeta').textContent=m.join('  ·  ');
   document.getElementById('nmfill').style.width=pct+'%';
 } else { nc.style.display='none'; }
 document.getElementById('steps').innerHTML = d.steps.map(s=>{
   let right = s.dur!=null ? s.dur.toFixed(1)+'m' : (s.status==='running'?'running…':'');
   if(s.sub){                      // forecast sub-progress
     const c = s.sub.current? ' · '+s.sub.current : '';
     right = (s.sub.total? s.sub.done+'/'+s.sub.total+' segments' : s.sub.done+' segments')+c;
   }
   if(s.subnm){                    // TCGplayer NM crawl sub-progress
     const p=s.subnm, pct = p.total_sets? Math.round(100*p.done_sets/p.total_sets):0;
     let bits = [pct+'%', p.done_sets.toLocaleString()+'/'+p.total_sets.toLocaleString()+' sets',
                 p.priced.toLocaleString()+' priced'];
     if(p.rate_cards) bits.push(Math.round(p.rate_cards).toLocaleString()+'/min');
     if(p.eta_min!=null) bits.push('ETA '+fmtEta(p.eta_min));
     if(p.game) bits.push(p.game);
     right = bits.join(' · ');
   }
   return `<li class=${s.status}><span class=ic>${s.status==='running'?'<span class=spin>◐</span>':IC[s.status]||'○'}</span>`+
     `<span class=name>${s.name}</span>`+
     `<span class=dur>${right}</span></li>`;
 }).join('');
 const t=document.getElementById('tail'); const atBottom=t.scrollTop+t.clientHeight>=t.scrollHeight-20;
 t.textContent=(d.tail||[]).join('\\n'); if(atBottom)t.scrollTop=t.scrollHeight;
}
tick(); setInterval(tick,2000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/status"):
            body = json.dumps(parse()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://localhost:{PORT}"
    print(f"refresh dashboard -> {url}  (Ctrl-C to stop)")
    if "--no-open" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
