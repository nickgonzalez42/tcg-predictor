import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";
// Customer-facing explainer of the forecasts: every idea leads with what it
// means for the collector, with the technical specifics kept alongside as
// optional depth for anyone who wants to audit the method.
export default function AboutPage() {
    usePageMeta("About", "What CardStock is and how its trading card price predictions work.");
  return (
    <article className="article full-span">
      <header className="article__head">
        <h1 className="article__title">How the forecasts work</h1>
        <div className="mono article__meta">
          Updated October 2026 · model version forecast-deep-v4.5 · retrained on every refresh
        </div>
        <p className="article__lede">
          CardStock treats trading cards like a market you can actually study. Every card gets a
          price forecast for 1 month, 6 months, and 1 year ahead, for the raw card and
          each graded tier, next to real graded price history and a portfolio that tracks your
          gains. Every Friday, a <Link to="/reports">market report</Link> rounds up the week's
          biggest movers, how each game is trending, and a public scorecard of how the model's own
          predictions have performed. This page explains where those forecasts come from, in plain language, with the
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
        <p>
          One thing you will sometimes see is a card with <em>no</em> regular forecast. A card
          needs at least two months of clean price history before the model will call where it is
          heading; a younger card shows its price and nothing more, rather than a guess dressed
          up as a forecast.
        </p>
        <p>
          Cards with no price <em>at all</em> yet are a different case and get a different
          treatment. An upcoming set still on pre-order, or one that released days ago, has no
          sales to anchor a forecast to, so instead each card gets a clearly labeled{" "}
          <strong>predicted launch price</strong> built only from what the card is. That
          estimate learns from every earlier launch in the same game: how its rarity has been
          pricing in the recent sets, what earlier cards of the same character fetched when they
          launched, how many cards in the set share its rarity, its printed stats, and its
          artwork (via the same CLIP embedding, including the launch prices of the cards it most
          resembles). Before anything is published, the method is tested on the game's most
          recent sets as if they were still unreleased, and the typical miss from that test is
          printed beside every estimate. Expect that miss to be large, often 50–100%: these are
          ballpark calls, much better at ranking a set's chase cards than at nailing a price,
          which is why they show a likely range and are framed in violet, never in the yellow of
          a market price. Every estimate is archived the day it is issued and graded against the
          card's first two months of real trading, so the pre-release calls carry a public track
          record of their own. Find them under the catalog's <strong>Pre-release</strong>{" "}
          filter.
        </p>
      </section>

      <section className="panel article__section">
        <h2>Where the numbers come from</h2>
        <p>
          The forecasts are only as trustworthy as the prices behind them, so both sources are
          public and checkable. What each card <em>is</em> (names, sets, rarities, stat lines,
          artwork) comes from TCGplayer's catalog. What each card <em>costs</em> comes from two
          places with a clear division of labor: raw (ungraded) prices are TCGplayer's own
          sales-backed market price, collected daily; graded prices (Grade 7 through PSA/BGS/CGC
          10) come from PriceCharting's per-grade sales history, which reaches back to roughly
          2020. The two are joined by exact product id, and a sanity check quarantines any match
          whose price is off from the card's historical reference by 25x or more, so a bad match
          leaves a card unpriced rather than mispriced.
        </p>
        <p>
          Prices also police each other. A market price is only trusted while real sales stand
          behind it — a listing nobody buys can sit frozen at any number, high or low. Every
          night the two sources are compared in both directions, and a card whose TCGplayer
          price has stopped moving while sales elsewhere disagree with it is flagged and
          reviewed; if the listing turns out to be fiction, the card's raw price switches to the
          sales-backed source instead. When neither source has defensible data, the card shows
          no price at all — we would rather show you nothing than a made-up number.
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
            <strong>The card itself</strong>: its rarity, its age, its printed stats (HP and
            attacks, cost and power, ink and lore, and so on), and its artwork, encoded for
            the model.
          </li>
          <li>
            <strong>Its own report card</strong>: if the model has been running too optimistic on
            this card or its set, it can see that and adjust. (Each card's and each set's trailing
            forecast error feeds back in as a feature on the next retrain.)
          </li>
        </ul>
        <p>
          Artwork is a special case worth calling out. Every card image is embedded with a CLIP
          vision model. Those embeddings power the "visually similar cards" comparisons in a
          forecast's reasoning, and as of v4.4 a compressed form of the same embedding is one of
          the model's inputs, so it can pick up on the kind of art-driven appeal — alt-art
          styles, fan-favorite characters — that never shows up in a stat line. In our own July
          2026 test the art signal moved accuracy only a little on its own; it stays in because
          art is a real part of what a card is worth, and the weekly report card will show
          whether it earns its keep. One consequence: a card with no artwork on file is not
          forecast at all.
        </p>
        <p>
          Under the hood, the engine is a gradient-boosted decision-tree model (scikit-learn's
          HistGradientBoostingRegressor), which handles cards with missing history gracefully and
          captures the way these signals interact. Higher-priced cards carry more training weight,
          so accuracy lands where the dollars are. All three horizons are trained directly on
          historical outcomes.
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
          not seen, and every graded outcome feeds back into the next day's retrain. Since October
          2026 the published number is also held to a flat-price baseline: the point forecast is
          scaled to what recently graded calls support, and the ranges are widened per volatility
          stratum until they cover 80% of outcomes in practice.
        </p>
        <p>
          The weekly <Link to="/reports">market report</Link> takes that accountability further
          with the model's report card: how far off a typical call was, whether it has been
          leaning optimistic or pessimistic, how often prices actually landed inside its
          low-to-high ranges, and how often it called the direction of a move correctly — overall
          and game by game. Those numbers are published every Friday whether they are flattering
          or not.
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
