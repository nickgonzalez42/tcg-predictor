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
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_GLOB = os.path.expanduser("~/Library/Logs/tcg-predictor/refresh-*.log")
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


def newest_log():
    files = glob.glob(LOG_GLOB)
    return max(files, key=os.path.getmtime) if files else None


def parse():
    path = newest_log()
    if not path:
        return {"state": "idle", "steps": [], "tail": [], "total": None,
                "started": None, "log": None}
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

    # The forecast step is the long one — give it its own sub-progress so the UI
    # isn't a static spinner for an hour. Prefer the explicit "[forecast] i/N
    # segments" counter; fall back to counting completed "[game/tier] N rows".
    frac_in_step = 0.0
    if running and running["name"] == "forecast":
        sub = _forecast_progress(lines)
        running["sub"] = sub
        if sub and sub["total"]:
            frac_in_step = sub["done"] / sub["total"]

    # Overall percent: completed top-level steps plus the running step's own
    # fraction, over the total step count.
    pct = None
    if total:
        pct = round(100 * (done_n + frac_in_step) / total)
    return {"state": state, "steps": steps, "total": total, "done": done_n,
            "pct": pct, "started": started, "log": os.path.basename(path),
            "tail": lines[-TAIL_LINES:]}


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
.ic{width:18px;text-align:center}.dur{margin-left:auto;color:#8a93a6;font-variant-numeric:tabular-nums;font-size:12px}
.done .ic{color:#3fd98a}.running .ic{color:#5cc3ff}.stopped .ic{color:#ff7a7a}
.name{text-transform:uppercase;letter-spacing:.03em;font-size:12.5px}
.running .name{color:#5cc3ff;font-weight:600}
pre{background:#070a12;border:1px solid #151b28;border-radius:8px;padding:12px 14px;
 font:11.5px/1.5 ui-monospace,Menlo,monospace;color:#c7cede;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.spin{display:inline-block;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
.foot{color:#5a6274;font-size:11.5px;margin-top:10px}
</style></head><body><div class=wrap>
<h1>Daily refresh <span id=badge class=badge></span></h1>
<div class=sub id=sub></div>
<div class=bar><i id=fill style=width:0></i></div>
<ul id=steps></ul>
<pre id=tail></pre>
<div class=foot>Auto-updates every 2s · safe to close this tab anytime — the refresh keeps running.</div>
</div><script>
const IC={done:'✓',running:'◐',stopped:'✕',pending:'○'};
async function tick(){
 let d; try{d=await (await fetch('/api/status')).json()}catch(e){return}
 const b=document.getElementById('badge');
 b.className='badge '+d.state; b.textContent=d.state;
 document.getElementById('sub').textContent =
   (d.log?d.log+' · ':'')+(d.started?'started '+d.started+' · ':'')+
   (d.total?(d.done+' of '+d.total+' steps done'):(d.done+' steps done'));
 document.getElementById('fill').style.width = (d.pct!=null?d.pct:0)+'%';
 document.getElementById('steps').innerHTML = d.steps.map(s=>{
   let right = s.dur!=null ? s.dur.toFixed(1)+'m' : (s.status==='running'?'running…':'');
   if(s.sub){                      // forecast sub-progress
     const c = s.sub.current? ' · '+s.sub.current : '';
     right = (s.sub.total? s.sub.done+'/'+s.sub.total+' segments' : s.sub.done+' segments')+c;
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
