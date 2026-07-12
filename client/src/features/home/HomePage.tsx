import { Link } from "react-router-dom";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { useUserInfoQuery } from "../account/accountApi";
import { gameKey } from "../../lib/util";
import type { Card } from "../../app/models/card";
import Sparkline from "../../app/shared/components/Sparkline";
import PricePair from "../../app/shared/components/PricePair";
import ChangePill from "../../app/shared/components/ChangePill";
import { fallbackToCardBack } from "../../lib/cardImages";

// Hero chart: solid blue history from the top mover's real sparkline, continued
// by a dashed gold segment to its 12m forecast price. Decorative only.
function HeroChart({ mover }: { mover?: Card }) {
    const W = 560, H = 210, PAD = 14;
    const hist = mover?.sparkline?.length ? mover.sparkline : [42, 45, 44, 49, 52, 50, 56, 61, 58, 66, 71, 74];
    const fcstEnd = mover?.fcst12To ?? hist[hist.length - 1] * 1.18;

    const all = [...hist, fcstEnd];
    const min = Math.min(...all), span = Math.max(...all) - min || 1;
    // history takes ~70% of the width, the forecast continuation the rest
    const histW = (W - PAD * 2) * 0.7;
    const x = (i: number) => PAD + (i / (hist.length - 1)) * histW;
    const y = (v: number) => PAD + (1 - (v - min) / span) * (H - PAD * 2);

    const histPts = hist.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const lastX = x(hist.length - 1), lastY = y(hist[hist.length - 1]);

    return (
        <svg className="hero__chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
            <polyline className="hero__history" points={histPts} />
            <line
                className="hero__forecast"
                x1={lastX} y1={lastY}
                x2={W - PAD} y2={y(fcstEnd)}
            />
        </svg>
    );
}

function MoverTile({ mover }: { mover: Card }) {
    const path = `/catalog/${gameKey(mover.game)}/${mover.id}`;
    return (
        <Link to={path} className="mover panel">
            <img
                className="mover__thumb"
                src={mover.pictureUrl} alt=""
                onError={e => fallbackToCardBack(e, mover.game, mover.cardType)}
            />
            <div className="mover__info">
                <div className="mover__name">{mover.name}</div>
                <div className="mono">{[mover.setName, mover.rarity].filter(Boolean).join(' · ')}</div>
                <div className="mover__row">
                    <span className="mover__price">
                        {/* no asOf here: mover tiles stay compact, date lives on the card page */}
                        <PricePair price={mover.price} forecast={mover.fcst12To} horizon="12M" />
                    </span>
                    <span className="mover__market"
                        title={`Price history over the past ${(mover.trendPeriod ?? '1y').toUpperCase()}`}>
                        {mover.trendPct != null && (
                            <span className="mover__trend">
                                <ChangePill value={mover.trendPct}
                                    title={`Price change over the past ${(mover.trendPeriod ?? '1y').toUpperCase()}`} />
                                <span className="mono">PAST {(mover.trendPeriod ?? '1y').toUpperCase()}</span>
                            </span>
                        )}
                        <Sparkline points={mover.sparkline} />
                    </span>
                </div>
            </div>
        </Link>
    );
}

const HOW_IT_WORKS = [
    { n: '1', title: 'Browse', text: 'Every card across seven TCGs — One Piece, Pokémon, Yu-Gi-Oh!, Magic, Lorcana, Digimon, Gundam — with live ungraded and graded price tiers.' },
    { n: '2', title: 'Forecast', text: '6 and 12 month ML price predictions with confidence bands and plain-English reasoning.' },
    { n: '3', title: 'Track', text: 'A brokerage-style portfolio with P/L, plus a watchlist with price alerts.' },
];

export default function HomePage() {
    const { data: movers } = useFetchMoversQuery(12);
    const { data: user } = useUserInfoQuery();
    const tiles = movers?.slice(0, 4) ?? [];
    const fanned = movers?.slice(0, 3) ?? [];

    return (
        <>
            <section className="hero subgrid full-span">
                <div className="hero__copy">
                    <h1 className="hero__title">The stock market for trading cards.</h1>
                    <p className="hero__sub">
                        Machine-learned price forecasts for cards across seven TCGs —
                        graded tiers, price history, and a portfolio that tracks your P/L.
                    </p>
                    <div className="btn-group">
                        {user
                            ? <Link className="btn" to="/portfolio">Open your portfolio</Link>
                            : <Link className="btn" to="/register">Start tracking free</Link>}
                        <Link className="btn btn--outline" to="/catalog">Browse the market</Link>
                    </div>
                </div>
                <div className="hero__panel">
                    <HeroChart mover={movers?.[0]} />
                    <div className="hero__fan">
                        {fanned.map((m, i) => (
                            <img
                                key={`${m.game}-${m.id}`}
                                className={`hero__card hero__card--${i}`}
                                src={m.pictureUrl} alt=""
                                onError={e => fallbackToCardBack(e, m.game, m.cardType)}
                            />
                        ))}
                    </div>
                </div>
            </section>

            {tiles.length > 0 && (
                <section className="full-span subgrid">
                    <h2 className="home__heading full-span">
                        Top movers <span className="est-note">· 12 month model forecast</span>
                    </h2>
                    <div className="movers subgrid">
                        {tiles.map(m => <MoverTile mover={m} key={`${m.game}-${m.id}`} />)}
                    </div>
                </section>
            )}

            <section className="full-span subgrid">
                <h2 className="home__heading full-span">How it works</h2>
                <div className="how subgrid">
                    {HOW_IT_WORKS.map(step => (
                        <div className="how__step" key={step.n}>
                            <div className="how__title">{step.n} · {step.title}</div>
                            <p className="how__text">{step.text}</p>
                        </div>
                    ))}
                </div>
            </section>
        </>
    );
}
