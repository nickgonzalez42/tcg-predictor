#!/usr/bin/env python3
"""
Claude-written prose for the per-game articles (2026-11-06, user request:
"two reports per game, per week ... a bit more like a traditional article
with a mix of images, infographics, and text").

Same contract as report_editorial.py: game_articles.py computes every number
and hands them over as a facts pack; one headless `claude -p` call writes the
headline, the lede, one paragraph per section, a header per section, the
picture captions and the closing line. The model may only restate numbers
from the pack, never invent a figure, a cause or a news event; the output is
strict JSON of plain-text strings, tag-stripped here and HTML-escaped at the
injection site; any failure leaves the caller on its templated sentences.

Voice: the classic Cracked.com house style the user installed as a skill on
2026-11-06 (classic-cracked-voice: a flat fact, then one swerve; a governing
metaphor in the first sentence; "you" prods, "we" confesses; one refrain;
captions as a second joke track; Title Case thesis headers). Profanity is
deliberately OFF: this is a public site with an AdSense application pending.

Set TCG_REPORT_PROSE=0 to disable (shared switch with the weekly reports).
"""

import json
import os
import re
import subprocess
import sys

from report_editorial import LORE, _clean, _find_cli, _headline

LIMITS = {"headline": 12, "lede": 85, "closer": 32}
SECTION_LIMIT = 95
CAPTION_LIMIT = 20
HEADER_LIMIT = 9

VOICE = """\
Voice: the classic Cracked.com house style. A deadpan explainer who has done \
the reading. Open the lede with ONE governing metaphor that reframes the \
subject (a set as a heist movie, a game as a police lineup, a rarity as a \
tax bracket), extend it for one more sentence, then go straight into the \
numbers. That is the article's only metaphor (user rule, 2026-11-10): bring \
it back sparingly, two or three light callbacks across the whole piece at \
most, never a fresh metaphor for a new section, and none at all while \
listing numbers. The base unit is a plainly stated, checkable fact followed by ONE \
comic swerve. Not three jokes per fact. One. "You" prods the reader with \
hypotheticals about what they were doing; "we" includes the site in the \
stupidity. A sincere point lasts one or two sentences, backed by a number, \
then a joke; never end a section on the sincere sentence. Read card names \
and keywords literally against their price moves (a card called Destroyed \
going down, a card called Victory losing). Pop culture as evidence, one \
reference per joke, roughly 1980 to 2015, only where it makes the thing \
instantly legible. Pick ONE refrain, a one or two word deadpan sentence, and \
use it two or three times across the piece as a callback. Headers are Title \
Case thesis-jokes, a declarative claim that is also funny, never a pun for \
its own sake. Long clause-stacked setup sentences with parentheticals, then \
a fragment. The fragment is the punch. End the piece on the joke: no \
summary, no moral, no "in conclusion". Captions are a second joke track: an \
imagined first-person line from the card, a deadpan false label, or a \
one-word deflation. Targets are the market, the cards, the hobby and us; \
never the reader as a person, never a real company or player by name.\
"""

RULES = """\
Hard rules:
- Use ONLY numbers present in the facts pack, rounded exactly as given. \
Never invent a figure, a cause, a news event, a tournament result, a ban, a \
reprint, or any reason for a move. You may set facts from the pack side by \
side; you may not assert an outside explanation.
- No profanity of any kind, no slurs, no sexual content. No buy, sell or \
hold advice, no "now is the time", no urgency, no exclamation points.
- NEVER use an em dash or an en dash anywhere. Use a period, comma, colon, \
semicolon, or parentheses instead.
- Plain text only: no HTML, no markdown, no links, no quotation marks \
around card names. Name cards exactly as the pack spells them.
- Never use the words "graded", "cohort", "calibration", "P/L" or "cost \
basis". Say "forecast" or "call", "checked", "inside the range".
- "We" is CardStock; "the model" is our price forecaster, and it is allowed \
to be wrong in print.\
"""

PROMPT = """You are the editor of CardStock, a trading-card price site, writing one \
article for collectors. Below is the complete, machine-computed facts pack \
(JSON) for this article. Write the prose around those numbers.

{voice}

{rules}

{lore}

The article is a {kind_desc}. Its sections, in order, each with a picture \
or a chart the caption goes under:
{sections}

Return ONLY a JSON object (no code fences, no commentary) with:
- "headline": the article title, max {headline} words, Title Case, no final \
period, no quotation marks, no date. It must name {headline_names} and carry \
one real number from the pack. Shapes that work: "Why 45 Falling Star Trek \
Cards Prove Magic Is Fine, Probably", "The 6 Cards Keeping Heroines Vol. 2 \
Above Water (Barely)".
- "lede": 3 to 4 sentences opening the article (max {lede} words): the \
governing metaphor, then the headline numbers from "totals".
- "sections": object mapping each section key above to one paragraph of 3 \
to 5 sentences (max {section} words each) grounded in that section's facts.
- "headers": object mapping each section key to its Title Case header (max \
{header} words).
- "captions": object mapping each section key to one caption for its picture \
or chart (max {caption} words).
- "closer": one or two sentences that end the article on the joke (max \
{closer} words), ideally the refrain.

FACTS:
{facts}
"""


KIND_DESC = {
    "set": ("report on how ONE card set (the game's newest set with at least a week of prices) "
            "is doing: its chase cards, this week's movers, the rarities, its path since release "
            "and what the model expects next"),
    "game": ("weekly report on ONE game's whole market: how the week went, which sets moved, the "
             "biggest movers, the groups that moved together, where the prices sit, and how the "
             "model has been doing on this game"),
    # 2026-11-07: the set slot's other focus, see game_articles.build_recent_article.
    "recent": ("report on ONE game's OTHER new cards: every priced card released in the last three "
               "months that is NOT in the newest set (the pack's scope says exactly): promos, "
               "starter decks, side products and the set before it; their chase cards, this week's "
               "movers, the releases they come from, where the prices sit and what the model expects "
               "next. The newest set has its own article; this one is about everything else new"),
}


def _write(kind, facts, sections, headline_names):
    """kind 'set' | 'game' | 'recent'; sections = [(key, description)]; returns dict or None."""
    if os.environ.get("TCG_REPORT_PROSE", "1") == "0":
        print("article_editorial: disabled via TCG_REPORT_PROSE=0")
        return None
    cli = _find_cli()
    if not cli:
        print("article_editorial: claude CLI not found, keeping templated text")
        return None
    prompt = PROMPT.format(
        voice=VOICE, rules=RULES, lore=LORE, kind_desc=KIND_DESC[kind],
        sections="\n".join(f'- "{k}": {d}' for k, d in sections),
        headline=LIMITS["headline"], lede=LIMITS["lede"], section=SECTION_LIMIT,
        header=HEADER_LIMIT, caption=CAPTION_LIMIT, closer=LIMITS["closer"],
        headline_names=headline_names, facts=json.dumps(facts, indent=1))
    try:
        out = subprocess.run(
            [cli, "-p", "--model", "claude-sonnet-5", "--output-format", "text"],
            input=prompt, capture_output=True, text=True,
            timeout=int(os.environ.get("TCG_PROSE_TIMEOUT", "600")))
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"article_editorial: CLI failed ({type(e).__name__}), keeping templated text",
              file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"article_editorial: CLI exit {out.returncode}: {out.stderr.strip()[:200]}",
              file=sys.stderr)
        return None
    raw = out.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        print("article_editorial: no JSON in reply, keeping templated text", file=sys.stderr)
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        print("article_editorial: bad JSON, keeping templated text", file=sys.stderr)
        return None

    prose = {k: _clean(data.get(k), cap) for k, cap in LIMITS.items()}
    prose["headline"] = _headline(prose.get("headline"))
    keys = [k for k, _ in sections]

    def pick(field, cap):
        src = data.get(field)
        if not isinstance(src, dict):
            return {}
        return {k: t for k, t in ((k, _clean(src.get(k), cap)) for k in keys) if t}

    prose["sections"] = pick("sections", SECTION_LIMIT)
    prose["headers"] = {k: _headline(v) or v for k, v in pick("headers", HEADER_LIMIT).items()}
    prose["captions"] = pick("captions", CAPTION_LIMIT)
    kept = sum(1 for k in LIMITS if prose[k]) + sum(
        len(prose[f]) for f in ("sections", "headers", "captions"))
    print(f"article_editorial: kept {kept} prose field(s) "
          f"({len(prose['sections'])}/{len(keys)} sections)")
    return prose


def write_set_article(facts, sections):
    return _write("set", facts, sections, "the set (its short name is fine)")


def write_game_article(facts, sections):
    return _write("game", facts, sections, "the game")


def write_recent_article(facts, sections):
    return _write("recent", facts, sections, "the game (naming one of the releases is fine)")
