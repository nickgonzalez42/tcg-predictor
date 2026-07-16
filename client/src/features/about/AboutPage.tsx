import { usePageMeta } from "../../lib/usePageMeta";
// Customer-facing explainer of the forecasts: every idea leads with what it
// means for the collector, with the technical specifics kept alongside as
// optional depth for anyone who wants to audit the method.
export default function AboutPage() {
    usePageMeta("About", "What cardstock is and how the AI price forecasts work.");
  return (
    <article className="article full-span">
      <header className="article__head">
        <h1 className="article__title">How the forecasts work</h1>
        <div className="mono article__meta">
          Updated July 2026 · model version forecast-deep-v4.3 · retrained daily
        </div>
        <p className="article__lede">
          cardstock treats trading cards like a market you can actually study. Every card gets a
          price forecast for 1 week, 1 month, 6 months, and 1 year ahead, for the raw card and
          each graded tier, next to real graded price history and a portfolio that tracks your
          gains. This page explains where those forecasts come from, in plain language, with the
          technical details alongside for anyone who wants to look under the hood. None of it is
          financial advice.
        </p>
      </header>

      <section className="panel article__section">
        <h2>What you see on a card</h2>
        <p>
          Open any card and each forecast gives you four things: a target price ("in six months
          this card is likely around $X"), a realistic low-to-high range, a confidence label, and
          a short plain-English reason for the call. The idea is to give you one informed opinion
          to weigh alongside your own, the same way you might check a weather forecast before
          planning your weekend.
        </p>
        <p>
          That weather comparison is close to how the model actually works. A meteorologist has
          never seen tomorrow, but they have studied thousands of past days and learned which
          patterns tend to precede rain. This model has studied millions of card-months of price
          history and learned which patterns tend to precede a card climbing or sinking. In
          technical terms, for each game, grade tier, and horizon it predicts the card's future{" "}
          <em>log-return</em>, r = log(P<sub>t+h</sub> / P<sub>t</sub>), and shows you the target
          price P<sub>t</sub>·e<sup>r</sup>.
        </p>
      </section>

      <section className="panel article__section">
        <h2>Where the numbers come from</h2>
        <p>
          The forecasts are only as trustworthy as the prices behind them, so both sources are
          public and checkable. What each card <em>is</em> (names, sets, rarities, stat lines,
          artwork) comes from TCGplayer's catalog. What each card has <em>cost</em> comes from
          PriceCharting: monthly per-grade price history reaching back to roughly 2020 (ungraded
          plus Grade 7, 8, 9, 9.5, and PSA/BGS/CGC/SGC 10), refreshed with daily snapshots. The
          two are joined by exact product id, and a sanity check quarantines any match whose price
          is off from the card's historical reference by 25x or more, so a bad match leaves a card
          unpriced rather than mispriced.
        </p>
      </section>

      <section className="panel article__section">
        <h2>What the model weighs</h2>
        <p>When it sizes up your card, the model looks at roughly what a sharp collector would:</p>
        <ul>
          <li>
            <strong>Its price journey</strong>: is it climbing, cooling off, sitting near an
            all-time high, or digging out of a crash? Fresh print or long-established?
            (Technically: price level, 1-month, 3-month and 1-year momentum, 6-month volatility,
            history length, and drawdown from the running high.)
          </li>
          <li>
            <strong>Its family</strong>: how is the rest of its set doing, and is this card
            pricey or cheap next to its set-mates? (A per-set price index, the set's momentum, and
            the card's price relative to its set.)
          </li>
          <li>
            <strong>The room</strong>: is this game hot right now, and is the whole hobby hot? (A
            hobby-wide index built from the median monthly return of every game we track, plus the
            card's own game momentum and the gap between them.)
          </li>
          <li>
            <strong>The card itself</strong>: its rarity, its age, and its printed stats (HP and
            attacks, cost and power, ink and lore, and so on), encoded for the model.
          </li>
          <li>
            <strong>Its own report card</strong>: if the model has been running too optimistic on
            this card or its set, it can see that and adjust. (Each card's and each set's trailing
            forecast error feeds back in as a feature on the next retrain.)
          </li>
        </ul>
        <p>
          Artwork is a special case worth calling out. Every card image is embedded with a CLIP
          vision model, and those embeddings still power the "visually similar cards" comparisons
          in a forecast's reasoning. We also tried feeding the artwork straight into the price
          model. A July 2026 test showed it did not improve accuracy, so the live model leaves it
          out and predicts from price behavior and card traits alone.
        </p>
        <p>
          Under the hood, the engine is a gradient-boosted decision-tree model (scikit-learn's
          HistGradientBoostingRegressor), which handles cards with missing history gracefully and
          captures the way these signals interact. Higher-priced cards carry more training weight,
          so accuracy lands where the dollars are. The 1, 6, and 12-month forecasts are trained
          directly on history; the 1-week figure is the 1-month call pro-rated to 7 days, since
          the price history is monthly and a true weekly model has no targets yet.
        </p>
      </section>

      <section className="panel article__section">
        <h2>How sure it is</h2>
        <p>
          The confidence label tells you how much to lean on a forecast. When the model's optimistic
          and pessimistic cases mostly agree, you get a tight range and a high-confidence label.
          When the honest answer is "this could go several ways," you get a wide range and a low
          label, and the range matters more than the single number. Technically, two companion
          models predict the 10th and 90th percentile outcomes to form an 80% range, widened by a
          conformal step calibrated on past errors; a range width up to 0.40 in log-return space
          reads as high confidence, up to 0.90 medium, and wider than that low.
        </p>
      </section>

      <section className="panel article__section">
        <h2>How we keep it honest</h2>
        <p>
          You can check the model's track record yourself. Every forecast it publishes is saved,
          and once its date arrives, it is graded against what the price actually did. Those
          graded results are the colored past-forecast lines on each card's chart, showing what
          the model said next to what happened, so a miss is on display rather than quietly
          forgotten. Behind the scenes, the model is never allowed to peek at the answer while it
          learns: it trains on the past and is tested on the most recent stretch of history it has
          not seen, and every graded outcome feeds back into the next day's retrain.
        </p>
      </section>

      <section className="panel article__section">
        <h2>What it can't do</h2>
        <p>
          A forecast reads price behavior and card characteristics, so it cannot know about a
          reprint announcement, a ban, or a grading-population shock any more than a weather app
          knows about next month's storm. It does not watch listings, social buzz, or tournament
          results yet. Treat each forecast as a well-calibrated opinion and one input to your own
          judgment. It will be wrong sometimes, and it is not a guarantee or financial advice.
        </p>
      </section>
    </article>
  );
}
