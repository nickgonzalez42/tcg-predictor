# cardstock.guide voice guide

One sentence: **a sharp collector talking to another collector, with receipts.**

House rule (2026-10-14, user decision): **never use an em dash or en dash,
anywhere on the site or in generated prose.** Use a period, comma, colon,
semicolon, or parentheses. The editorial and reasoning prompts state this and
their output scrubbers enforce it.

Modeled on two references (2026-10-14 analysis):
- **TCGplayer Infinite** market articles ("The Biggest Price Spikes in Pokémon This
  Week"): the register for **prose** (weekly report, About, card-page reasons,
  meta descriptions).
- **boxed.gg** product copy: the register for **UI** (feature names, badges,
  section headers, button labels).

---

## Prose voice (TCGplayer register)

What their articles actually do, with their own lines as evidence:

1. **Prices are journeys, never points.** Every number gets a from → to arc and
   a timeframe. *"went from $10 up to a new high of $23"*, *"hit $150 this time
   a year ago, jumped again to $300 at the beginning of the year, and made it
   all the way to $500 by August."* Never write "Price: $23."

2. **Kinetic verbs, one metaphor per sentence.** *taking off, leapt, dropping
   like a rock, taking a plunge, fell precipitously, popping back up, on a
   tear, bucking the trend, came out of the gates, settling in.* Pick one and
   stop; two metaphors in a sentence reads like parody.

3. **Cold-open with the number or the question.** *"$3,500 to $4,000. That's
   the price range for the sold listings of an RBG Mew."* Short fragment first,
   explanation second.

4. **Speculate openly, hedge honestly.** *"Maybe the demand for Gengar is being
   eaten up by the once-$1,000+ Mega Gengar ex... or maybe 30th Celebration
   simply has so many other good pulls. Either way, it's made Gengar more
   affordable."* Guess at causes, mark guesses as guesses, land on the fact.

5. **Community "we", named model.** *"the best pack opening experience we've
   seen"*, *"most of us assumed"*. "We" is the hobby; "the model" is the math.
   First person is welcome in the report when there's a real observation behind
   it, their writers mention Target drops and opening packs with their kids.

6. **Confident verdicts off the data.** *"make no mistake, these are running
   right around $1,000"*, *"we've officially got a starting point"*. When the
   data is in, say the thing.

7. **Precision is non-negotiable.** Exact prices, set names, card numbers
   (*"Night Pikachu 150 dipped from $90 to $65"*), real timeframes. The casual
   surface never costs a decimal. This is why the voice works: energy on top,
   receipts underneath.

8. **Cards get stories when they have one.** *"This is one of those Pokémon
   stories that's hard to believe."* A multi-year climb, an artist connection,
   a reprint wave, context beats adjectives.

**Our standing rule stays:** we disclose model limits (pre-release estimates
are ballpark, bands are honest). Disclose in the same confident register:
"We keep every forecast the model has ever published" is already on-voice.

## UI voice (boxed.gg register)

Their product copy barely uses sentences. What it does instead:

1. **Feature names are two words, max, and punchy.** *Battles, Forge, Hop or
   Drop, Luckback, Gem Drops, Missions.* Verb-energy over description.
2. **Wordplay in names, plain talk in rules.** Boxes called *"No Mana No
   Problem"*; terms stated flatly (*"gems have no cash value"*).
3. **Trust is a named feature, not a disclaimer.** *"Provably Fair"* is a tab;
   odds are plain data tables. Our equivalents (nightly grading, the public
   forecast archive, the scorecard) deserve names and badges, not footnotes.

**What we do NOT borrow from boxed.gg:** reviewers flag its near-miss spin
mechanics and casino psychology. We take the naming energy and the
trust-as-feature pattern; no urgency mechanics, no FOMO copy.

---

## Rewrites of real copy (before → after)

**Card-page reason text** (template in forecast_predict/reason builders):
- Before: *"Projects -0% over 1m. What moves it: its set (Base Set) is up 84%
  over the past year; cards with similar art gained ~53% over the past year
  (e.g. Charizard, Kadabra); 3-month momentum -24%."*
- After: *"Flat month ahead. Base Set has climbed 84% this year and lookalike
  art (Charizard, Kadabra) is up ~53%; but this card's own 3-month momentum
  is running -24%, and that drag wins."*

**Weekly report movers lead-in** (market_report.py):
- Before: *"The cards our 1-month model is most optimistic about right now:"*
- After: *"The climbs the model is most confident in this month:"*

**Pre-release section intro** (market_report.py):
- Before: *"Cards that haven't started trading yet, presale sets and..."*
- After: *"No sales yet, no problem: here's where the model expects presale
  cards to land once they start trading."*

**Trust surfaces** (site UI): name them. Scorecard/accuracy page → **"Track
Record"**; archive note → **"Every forecast, kept"**; nightly refresh →
**"Graded nightly"**.

## Checklist before shipping copy

- [ ] Every price has a from → to and a timeframe
- [ ] One metaphor max per sentence; zero in tables
- [ ] No em or en dashes, anywhere
- [ ] Guesses marked as guesses; verdicts stated plainly
- [ ] Numbers exact; set names and card numbers real
- [ ] UI names ≤ 2 words; trust features named, not footnoted
- [ ] No urgency/FOMO mechanics
