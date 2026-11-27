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

from report_editorial import LORE, _clean, _find_cli, _headline, writer_cmd, writer_timeout

LIMITS = {"headline": 12, "lede": 85, "closer": 32}
SECTION_LIMIT = 95
# The writer is told the limits above and lands on them: the median section
# in 66 saved replies (2026-11-07..27) is 93 words. A field over its limit
# is dropped whole and the templated line stands in, which threw away 23 of
# 405 written sections, 16 of them for being 1 to 10 words over (the stub
# paragraphs in the Pokemon Secret and Flesh and Blood Marvel articles). A
# section or a lede may run this many words over. A section longer still is
# cut back to the whole sentences that fit (a 119-word sales paragraph was
# two sentences of 104 words and a closing line); only when too little is
# left, under KEEP_LEAST of the limit, does it drop, and it says so.
OVER = 10
KEEP_LEAST = 0.6
_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|No)\.$")


def _trim(text, limit):
    """text cut back to whole sentences within limit words; None when fewer
    than KEEP_LEAST of SECTION_LIMIT would be left or nothing can be cut."""
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    while len(sents) > 1:
        sents.pop()
        kept = " ".join(sents)
        if _ABBREV.search(kept):                  # "... from Dr." is not an ending
            continue
        if len(kept.split()) <= limit:
            return kept if len(kept.split()) >= KEEP_LEAST * SECTION_LIMIT else None
    return None
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
- Never use the words "graded", "horizon", "cohort", "calibration", "P/L" \
or "cost basis" (a card or set name that contains one is spelled as the pack \
spells it). Say "forecast" or "call", "checked", "window", "inside the range".
- Numbers in one style: a percentage is the figure and the word percent \
(16.9 percent), a fall is "down 16.9 percent" or "-16.9 percent", never \
"negative 16.9" and never the % sign; money is $1,594, never "1,594 dollars".
- Never end a paragraph, the lede or the closer on a short tag line that \
begins with "Still" ("Still undercard.", "Still falling."). End on the fact.
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

{recent}CLAIMS: say only what the facts measure (user rule, 2026-11-11). A comparison \
is named by what it covers ("the last five releases", "the six sets we can \
compare at this age"); never "every", "all" or "any" earlier set, never "ever", \
"of all time", "in history" or "record", unless a fact states exactly that.

Return ONLY a JSON object (no code fences, no commentary) with:
- "headline": the article title, max {headline} words, Title Case, no final \
period, no quotation marks, no date. It must name {headline_names} and carry \
one real number from the pack. The title is straight (user rule, 2026-11-10: \
"tone down the snarkiness on the titles"): a plain, factual statement of the \
main finding, the kind a newspaper's markets page would run; the voice lives \
in the body. No tag line after a comma, no "Probably", "Somehow", "As \
Advertised" or "Nobody Sends Help", no joke. When the facts carry "context" \
(user rule, 2026-11-10: most sets fall after release, so a drop alone is not \
news), the title states the set's standing against the earlier sets at the \
same age, better or worse than typical, with the number: "Heroines Vol. 2 Is \
Down 16.9 Percent, Holding Up Better Than Most One Piece Sets at Day 8", \
"Reality Fracture Is Down 44 Percent, Worse Than Every Recent Magic Set at \
This Age", "Hyperia City Is Tracking a Typical Lorcana Set a Month In". \
Without "context": "Reality Fracture's Chase Cards Fell 44.3 Percent in a \
Month".
- "lede": 3 to 4 sentences opening the article (max {lede} words): the \
governing metaphor, then the headline numbers from "totals", and, when \
"context" is present, the standing against the earlier sets at this age in \
plain words (what a typical set of this game had done by now, and where this \
one sits).
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
    # 2026-11-14: one kind of chase card across every set, see type_articles.py.
    "type": ("report on ONE KIND of card in one game (card_type, what_it_is), followed over its whole "
             "priced history rather than one week: what the group is worth today, its price index month "
             "by month against the game's other expensive cards, how each release's cards have done and "
             "the typical path by age, a few example cards, and what the model expects next. There is no "
             "'this week' here; the time spans are months and years, named as the pack names them. The "
             "headline names the game and the card type and states ONE figure, the one the pack gives as "
             "headline_figure (its 'what' says what it measures, so the wording matches: a change over "
             "the past year, a change since a named month, or a median price); the picture above the "
             "article shows that same figure, so no other number goes in the headline. what_it_is is "
             "our own one-line description of the kind of card, not a measurement: use it to say what "
             "the card is, and take every count and date from totals and per_set"),
}


# What the last few articles already used (2026-11-24). Every article is
# written alone, so the same lines kept coming back: "Three Cards, Three Very
# Different Years" headed three card-type articles in a week, "Most of X
# Costs Less Than a Dollar" five game reports, and nine of the last twenty
# articles took a "Still ___." refrain. game_articles.recent_phrases reads
# the stored articles and sets this before each one is written.
_RECENT = {}
RECENT_BLOCK = """\
ALREADY USED by our last few articles. Do not reuse any of these, and do not \
reuse one with a word swapped ("Still sealed." after "Still falling." is the \
same line; so is "Most of Lorcana Costs Less Than a Dollar" after "Most of \
Gundam's Cards Live Under a Dollar"). Find a header that says what THIS \
section found and a refrain built on a different word:
- headers: {headers}
- refrains and closing lines: {refrains}

"""


def set_recent(used):
    """used = {"headers": [...], "refrains": [...]} or None."""
    _RECENT.clear()
    _RECENT.update(used or {})


def _recent_block():
    headers, refrains = _RECENT.get("headers") or [], _RECENT.get("refrains") or []
    if not headers and not refrains:
        return ""
    return RECENT_BLOCK.format(headers=" | ".join(headers) or "(none)",
                               refrains=" | ".join(refrains) or "(none)")


# One style for numbers and words, whatever the writer sent (2026-11-24). The
# rules ask for it; this makes sure. Across the first 34 articles the same
# paragraph could carry "16.9 percent", "63.5%", "624.12 dollars" and
# "negative 2.1 percent", and "graded" got through in a caption. Our own
# sentences say "+1.2 percent" and "$624.12", so that is the style.
_SWAPS = (("graded", "checked"), ("cohorts", "groups"), ("cohort", "group"))


# The writer's stock closer: "Still undercard." ended the lede, two
# sections and the closer of one article (2026-11-27), after the review of
# 2026-11-24 had found it across articles. The rule forbids it; this takes
# out the ones that slip through. Only a whole sentence of "Still" and one
# to three plain words goes: "Still, the index is up" and "Still up 4.1
# percent." stay.
_STILL = re.compile(r"(?:(?<=[.!?:;])\s+|^)Still(?: [A-Za-z'’-]+){1,3}\.(?=\s|$)")


def _style(text, title=False):
    if not text:
        return text
    text = re.sub(r"(\d)\s?%", r"\1 Percent" if title else r"\1 percent", text)
    text = re.sub(r"\b(\d[\d,]*(?:\.\d+)?) dollars\b", r"$\1", text, flags=re.I)
    text = re.sub(r"\bnegative (?=\d)", "-", text, flags=re.I)
    text = re.sub(r"\bpositive (?=\d)", "+", text, flags=re.I)
    for bad, good in _SWAPS:
        text = re.sub(rf"\b{bad}\b", good, text)
        text = re.sub(rf"\b{bad.capitalize()}\b", good.capitalize(), text)
    # Lower case only: "Modern Horizons 3" is a set's name.
    text = re.sub(r"\bhorizon(s?)\b", r"window\1", text)
    if not title:
        text = _STILL.sub("", text).strip()
    return text


def _write(kind, facts, sections, headline_names):
    """kind 'set' | 'game' | 'recent' | 'type'; sections = [(key, description)]; returns dict or None."""
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
        headline_names=headline_names, facts=json.dumps(facts, indent=1),
        recent=_recent_block())
    try:
        out = subprocess.run(
            writer_cmd(cli), input=prompt, capture_output=True, text=True,
            timeout=writer_timeout())
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

    compared = ((facts.get("context") or {}).get("compared_against") if isinstance(facts, dict) else None)
    prose = {k: _guard(_style(_clean(data.get(k), cap + (OVER if k == "lede" else 0)),
                              title=(k == "headline")), compared)
             for k, cap in LIMITS.items()}
    prose["headline"] = _headline(prose.get("headline"))
    keys = [k for k, _ in sections]

    def pick(field, cap, over=0):
        src = data.get(field)
        if not isinstance(src, dict):
            return {}
        title = field == "headers"
        out = {}
        for k in keys:
            text = _clean(src.get(k), cap + over)
            if text is None and field == "sections" and isinstance(src.get(k), str) and src[k].strip():
                whole = _clean(src[k], 10 ** 6) or ""
                text = _trim(whole, cap + over)
                if text:
                    print(f"article_editorial: section '{k}' cut to its first {len(text.split())} "
                          f"of {len(whole.split())} words", file=sys.stderr)
                else:
                    print(f"article_editorial: section '{k}' ran {len(whole.split())} words against "
                          f"{cap} (+{over}), keeping templated text", file=sys.stderr)
            text = _guard(_style(text, title), compared)
            if text:
                out[k] = text
        return out

    prose["sections"] = pick("sections", SECTION_LIMIT, OVER)
    prose["headers"] = {k: _headline(v) or v for k, v in pick("headers", HEADER_LIMIT).items()}
    prose["captions"] = pick("captions", CAPTION_LIMIT)
    kept = sum(1 for k in LIMITS if prose[k]) + sum(
        len(prose[f]) for f in ("sections", "headers", "captions"))
    print(f"article_editorial: kept {kept} prose field(s) "
          f"({len(prose['sections'])}/{len(keys)} sections)")
    return prose


# Overreach guard (2026-11-11, user: "Put in a block against this sort of
# overreaching"). A title said "Worse Than Every Earlier Set" when the facts
# compared five releases. A universal claim about earlier sets is rewritten
# to the comparison the facts name ("the last five releases"); one with no
# such name, or an "ever"/"of all time" superlative, drops that field so the
# templated text stands in.
_UNIVERSAL = re.compile(
    r"\b(?:every|all(?: of the)?|any)(?: single)? (?:earlier|previous|prior|other|past)"
    r"(?: [A-Z][\w.'-]*)*(?: sets?| releases?| expansions?)\b", re.I)
# "ever" alone is ordinary English ("ever since", "without ever"); only a
# superlative paired with it, or an all-time claim, is an overreach.
_SUPERLATIVE = re.compile(
    r"\b(?:best|worst|biggest|largest|steepest|sharpest|highest|lowest|strongest|weakest|fastest|deepest|"
    r"greatest|smallest|slowest)\b[^.;:]{0,40}?\bever\b|\bever (?:recorded|seen)\b|\bof all time\b|"
    r"\ball-time\b|\bin (?:the )?history of\b", re.I)


# "better than most sets" is only measured when the facts carry a context
# standing (game_articles.STANDING_MIN_PEERS earlier releases or more).
_MOST = re.compile(r"\b(?:better|worse|stronger|weaker|ahead of|behind|beat(?:s|ing)?|outperform(?:s|ing)?|trail(?:s|ing)?|lag(?:s|ging)?) (?:than )?most\b", re.I)


def _guard(text, compared):
    if not text:
        return text
    if not compared and _MOST.search(text):
        print(f"article_editorial: overreach dropped: {text[:90]!r}", file=sys.stderr)
        return None
    if _SUPERLATIVE.search(text):
        print(f"article_editorial: overreach dropped: {text[:90]!r}", file=sys.stderr)
        return None
    if _UNIVERSAL.search(text):
        if not compared:
            print(f"article_editorial: overreach dropped: {text[:90]!r}", file=sys.stderr)
            return None
        def repl(m):
            words = m.group(0).split()
            if len(words) > 1 and words[0][0].isupper() and words[1][0].isupper():
                return " ".join(w if w == "the" else w[0].upper() + w[1:] for w in compared.split())   # a Title Case line
            return compared[0].upper() + compared[1:] if words[0][0].isupper() else compared
        starts_upper = text[:1].isupper()
        text = _UNIVERSAL.sub(repl, text)
        if starts_upper:
            text = text[:1].upper() + text[1:]
    return text


def write_set_article(facts, sections):
    return _write("set", facts, sections, "the set (its short name is fine)")


def write_game_article(facts, sections):
    return _write("game", facts, sections, "the game")


def write_type_article(facts, sections):
    return _write("type", facts, sections, "the game and the card type (One Piece Manga Cards)")


def write_recent_article(facts, sections):
    return _write("recent", facts, sections, "the game (naming one of the releases is fine)")
