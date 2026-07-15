import { usePageMeta } from "../../lib/usePageMeta";
// Long-form explainer of the forecasting model, help-center style: one pass in
// exhaustive technical detail, then the same story in plain English.
export default function AboutPage() {
    usePageMeta("About", "What TCG Predictor is and how the AI price forecasts work.");
  return (
    <article className="article full-span">
      <header className="article__head">
        <h1 className="article__title">How the price forecasts work</h1>
        <div className="mono article__meta">
          Updated July 2026 · model version forecast-deep-v4.2 · retrained nightly
        </div>
        <p className="article__lede">
          Every card on this site carries machine-learned price forecasts — 1 week, 1 month,
          6 months, and 1 year ahead, for the raw card and each graded tier. This page explains
          exactly how those numbers are produced, twice: first in full technical detail for
          readers who want to audit the method, then in plain English for everyone else.
          Nothing here is a black box we won't describe, and nothing is financial advice.
        </p>
      </header>

      <section className="panel article__section">
        <h2>Part 1 — The full technical detail</h2>

        <h3>The data</h3>
        <p>
          Card identity (names, sets, rarities, stat lines, artwork) comes from TCGplayer's
          public catalog. Every price comes from PriceCharting: monthly per-grade price series
          reaching back to roughly 2020 for each card — ungraded plus Grade 7, 8, 9, 9.5, and
          PSA/BGS/CGC/SGC 10 — refreshed with daily snapshots. The two sources are joined by
          exact product id, and a sanity gate quarantines any match whose price disagrees with
          the card's frozen historical reference by 25× or more: better an unpriced card than a
          wrongly priced one.
        </p>

        <h3>What the model actually predicts</h3>
        <p>
          For each combination of game, grade tier, and horizon, a separate model predicts the
          card's future <em>log-return</em>: r = log(P<sub>t+h</sub> / P<sub>t</sub>). The
          displayed forecast price is P<sub>t</sub>·e<sup>r</sup>. Horizons of 1, 6, and 12
          months are trained directly against historical outcomes; the 1-week figure is the
          1-month prediction pro-rated to 7 days (price history is monthly, so a true weekly
          model has no training targets yet — every 1-week forecast discloses this in its
          reasoning). Predictions are clipped to ±10× to keep tail noise out of the rankings.
        </p>

        <h3>The learning algorithm</h3>
        <p>
          Each model is a gradient-boosted decision-tree ensemble (scikit-learn's
          HistGradientBoostingRegressor), chosen because it handles missing values natively —
          essential when cards differ in how much history and metadata they have — and captures
          non-linear interactions without hand-tuning. Alongside the main point-estimate model,
          two quantile models are trained on the same features for the 10th and 90th percentile
          outcomes, giving every card its own 80% scenario interval.
        </p>

        <h3>The features</h3>
        <ul>
          <li>
            <strong>Price trajectory</strong> — log price level, 1-month, 3-month and 1-year momentum,
            6-month volatility, history length, months since first price (new prints behave
            differently), and drawdown from the running all-time high.
          </li>
          <li>
            <strong>Set context</strong> — a per-set price index, the set's 3-month and 1-year
            momentum, and the card's price relative to its set-mates.
          </li>
          <li>
            <strong>Market context</strong> — a hobby-wide index built from the median monthly
            return of every game we track: global 3-month and 1-year momentum, the card's own
            game's momentum, and the spread between them (is this game running hot or cold
            relative to the whole hobby).
          </li>
          <li>
            <strong>Card identity</strong> — rarity, set, card type, release year, and the
            game-specific printed stat line (HP and attacks, cost and power, ink and lore, and
            so on), encoded categorically.
          </li>
          <li>
            <strong>The artwork itself</strong> — every card image is embedded with a CLIP
            vision model and compressed to 24 principal components, so visual style (full-art
            treatments, character renders, texture) participates in the prediction. The same
            embeddings power the "visually similar cards" comparisons in the reasoning.
          </li>
          <li>
            <strong>The model's own track record</strong> — once archived predictions mature,
            each card's (and each set's) trailing signed forecast error re-enters training as a
            feature, so systematic over- or under-shooting is visible to the next retrain.
          </li>
        </ul>

        <h3>Training and honest evaluation</h3>
        <p>
          Training samples are assembled walk-forward: for every historical month t, every card
          with a valid price at t and at t+h becomes one example, with features computed only
          from information available at t — nothing from the future leaks backward. Evaluation
          uses a strict temporal split: models are fit on samples anchored before January 2025
          and scored on everything after, so reported error reflects genuinely out-of-sample
          performance on the most recent market regime, not memorized history. After
          evaluation, production models retrain on all data. The entire fleet — every game,
          tier, and horizon — retrains from scratch on every pipeline run, so forecasts always
          reflect the newest prices, and very large games are capped at three million training
          rows by uniform subsampling.
        </p>

        <h3>Confidence, straight from the model</h3>
        <p>
          The confidence label on each forecast is not a heuristic: it is the width of the
          card's own 80% quantile interval in log-return space. A spread of at most 0.40 is
          labeled high confidence, up to 0.90 medium, anything wider low. The same interval is
          shown as the forecast's low and high scenario prices.
        </p>

        <h3>Self-review</h3>
        <p>
          Every first-issued forecast is archived permanently. Once its horizon elapses, a
          scorecard grades it against what the price actually did, recording the realized
          error, the signed bias, and whether reality landed inside the 80% interval (a
          calibrated model should score about 80% there). Those graded outcomes are what you
          see as the colored past-forecast lines on each card's chart — and, as described
          above, they feed back into the next retrain as features. The model is graded in
          public, by design.
        </p>

        <h3>What the model does not do</h3>
        <p>
          It does not see order books, listings, social sentiment, or tournament results (a
          signal hook exists for those, but none are live yet). It cannot anticipate reprint
          announcements, bans, or grading-population shocks. It extrapolates from price
          behavior and card characteristics — treat it as a well-calibrated statistical
          opinion, not an oracle.
        </p>
      </section>

      <section className="panel article__section">
        <h2>Part 2 — In plain English</h2>
        <p>
          Think of the model as a weather forecaster for card prices. A meteorologist doesn't
          know the future; they've studied thousands of past days and learned that certain
          pressure patterns tend to precede rain. Our model has studied millions of
          card-months of price history and learned which patterns tend to precede a card
          climbing or sinking.
        </p>
        <p>When it looks at your card, it considers roughly what a sharp collector would:</p>
        <ul>
          <li><strong>Its price journey</strong> — is it climbing, cooling off, sitting near its all-time high, or digging out of a crash? Fresh print or long-established?</li>
          <li><strong>Its family</strong> — how is the rest of its set doing? Is the card expensive or cheap compared to its set-mates?</li>
          <li><strong>The room</strong> — is this game hot right now? Is the whole hobby hot? A rising tide lifts most boats, and the model watches the tide.</li>
          <li><strong>The card itself</strong> — its rarity, its age, its stats, and literally what it looks like: the model "sees" each card's artwork and knows how visually similar cards have performed.</li>
          <li><strong>Its own report card</strong> — if the model has been consistently too optimistic about this card or its set, it knows that too, and adjusts.</li>
        </ul>
        <p>
          From all of that it produces a best guess — "in six months this card is likely
          around $X" — plus a realistic range around it. That range is where the confidence
          label comes from: when the range is tight, the model says <em>high</em>; when the
          honest answer is "could go a lot of ways," it says <em>low</em> rather than
          pretending.
        </p>
        <p>
          Two things keep it honest. First, during practice it is never allowed to peek at the
          answer: it trains on the past and is tested on price movements it has never seen,
          the same way a student is tested on new problems. Second, every prediction it
          publishes is saved forever and graded when its date arrives — you can see those
          grades yourself as the colored lines on any card's price chart, showing what the
          model said next to what actually happened. When it misses, the miss is visible, and
          the next night's model learns from it.
        </p>
        <p>
          It will still be wrong sometimes — it can't know a reprint is coming any more than a
          forecaster can know about next month's storm. Use it the way you'd use a good
          forecast: as one informed input to your own judgment, not a guarantee.
        </p>
      </section>
    </article>
  );
}
