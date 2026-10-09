#!/usr/bin/env python3
"""
Claude-written editorial for the weekly market report (2026-10-09, user
request: "more natural sounding language and analysis").

market_report.py computes every number exactly as before and passes them
here as a facts pack; one headless `claude -p` call (the locally installed
Claude Code CLI, so it runs on the user's existing subscription during the
Thursday-night refresh) writes the prose around them. The contract keeps
the report trustworthy:

  - the model may only restate numbers from the facts pack, never invent
    figures, causes, or news — observational language only;
  - output is strict JSON of plain-text strings; anything that fails to
    parse or validate is discarded wholesale;
  - returned text is tag-stripped here and HTML-escaped at the injection
    site, so nothing the model writes can reach the page as markup;
  - on ANY failure (no CLI, timeout, bad JSON) the caller keeps the
    templated sentences — the nightly can never be hurt by this step.

Set TCG_REPORT_PROSE=0 to disable.
"""

import json
import os
import re
import shutil
import subprocess
import sys

CLI_FALLBACKS = (
    os.path.expanduser("~/.claude/local/claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)

# Word caps per field — a report lede that runs long reads worse than the
# template it replaced. Oversized fields are dropped individually.
LIMITS = {"lede": 70, "overview": 140, "model_note": 50}
GAME_LIMIT = 55

PROMPT = """You are the editor of CardStock's weekly trading-card market \
report, read by collectors. Below is this week's complete, machine-computed \
facts pack (JSON). Write the editorial prose around those numbers.

Voice: a sharp, plainspoken market analyst. Lead with the story, not a \
recital of figures. Vary sentence structure. No hype, no buy/sell advice, \
no exclamation points.

Hard rules:
- Use ONLY numbers present in the facts pack, rounded exactly as given. \
Never invent a figure, a cause, a news event, or a reason for a move — you \
may describe WHAT moved, not speculate WHY.
- Plain text only: no HTML, no markdown, no links. Name cards and groups in \
ordinary words.
- Percent moves in the facts are week-over-week unless labeled otherwise.

Return ONLY a JSON object (no code fences, no commentary) with:
- "lede": 2-3 sentences opening the report — the week's headline story \
(max {lede} words).
- "overview": one paragraph after the lede telling the cross-game story: \
where the week's strength and weakness concentrated, notable rotations, \
what the drift chart will show (max {overview} words).
- "games": object mapping each game key (exactly the keys under "games" in \
the facts) to 1-2 sentences on that game's week — its trends and standout \
cards, grounded in its numbers (max {game} words each). Every game key \
must be present.
- "model_note": one sentence on the forecast model's live record given the \
"model" facts, honest about weak spots (max {model_note} words).

FACTS:
{facts}
"""


def _find_cli():
    path = shutil.which("claude")
    if path:
        return path
    for p in CLI_FALLBACKS:
        if os.path.exists(p):
            return p
    return None


def _clean(text, cap):
    """Plain text in, plain text out: strip tags, collapse whitespace,
    enforce the word cap. Returns None when the field should be dropped."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text.split()) > cap:
        return None
    return text


def write_editorial(facts):
    """facts dict -> {'lede','overview','games':{...},'model_note'} or None."""
    if os.environ.get("TCG_REPORT_PROSE", "1") == "0":
        print("report_editorial: disabled via TCG_REPORT_PROSE=0")
        return None
    cli = _find_cli()
    if not cli:
        print("report_editorial: claude CLI not found — keeping templated text")
        return None

    prompt = PROMPT.format(facts=json.dumps(facts, indent=1),
                           lede=LIMITS["lede"], overview=LIMITS["overview"],
                           game=GAME_LIMIT, model_note=LIMITS["model_note"])
    try:
        out = subprocess.run(
            [cli, "-p", "--model", "claude-sonnet-5", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"report_editorial: CLI failed ({type(e).__name__}) — "
              "keeping templated text", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"report_editorial: CLI exit {out.returncode}: "
              f"{out.stderr.strip()[:200]} — keeping templated text",
              file=sys.stderr)
        return None

    raw = out.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        print("report_editorial: no JSON in reply — keeping templated text",
              file=sys.stderr)
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        print("report_editorial: bad JSON — keeping templated text",
              file=sys.stderr)
        return None

    prose = {k: _clean(data.get(k), cap) for k, cap in LIMITS.items()}
    games = data.get("games")
    prose["games"] = ({g: t for g, t in
                       ((g, _clean(v, GAME_LIMIT)) for g, v in games.items())
                       if t and g in facts.get("games", {})}
                      if isinstance(games, dict) else {})
    kept = sum(1 for k in LIMITS if prose[k]) + len(prose["games"])
    print(f"report_editorial: kept {kept} prose field(s) "
          f"({len(prose['games'])}/{len(facts.get('games', {}))} games)")
    return prose
