import { Link } from "react-router-dom";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { useUserInfoQuery } from "../account/accountApi";
import { gameKey } from "../../lib/util";
import type { Card } from "../../app/models/card";
import Sparkline from "../../app/shared/components/Sparkline";
import PricePair from "../../app/shared/components/PricePair";
import ChangePill from "../../app/shared/components/ChangePill";
import { fallbackToCardBack } from "../../lib/cardImages";

// Hero animation: cycles through high-value movers. Each card's art rides the
// tip of its past-1Y price line as it draws itself; when the line reaches the
// present, the dotted forecast segment extends out; the scene holds, swipes
// away, and the next card begins. Decorative only.
const HERO = { draw: 3200, fcst: 900, hold: 1600, out: 500 };
const HERO_W = 560, HERO_H = 210, HERO_PAD = 16;

function HeroChart({ movers }: { movers?: Card[] }) {
    const cards = useMemo(() => {
        const ok = (movers ?? []).filter(m =>
            (m.price ?? 0) >= 20 && (m.sparkline?.length ?? 0) >= 4 && m.fcst12To != null);
        return [...ok].sort(() => Math.random() - 0.5);   // random order per visit
    }, [movers]);
    const [idx, setIdx] = useState(0);

    if (!cards.length) {
        // No qualifying movers yet — draw a static placeholder line.
        const pts = [42, 45, 44, 49, 52, 50, 56, 61, 58, 66, 71, 74];
        const min = Math.min(...pts), span = Math.max(...pts) - min || 1;
        const d = pts.map((v, i) =>
            `${(HERO_PAD + (i / (pts.length - 1)) * (HERO_W - HERO_PAD * 2) * 0.7).toFixed(1)},` +
            `${(HERO_PAD + (1 - (v - min) / span) * (HERO_H - HERO_PAD * 2)).toFixed(1)}`).join(' ');
        return (
            <svg className="hero__chart" viewBox={`0 0 ${HERO_W} ${HERO_H}`} aria-hidden="true">
                <polyline className="hero__history" points={d} />
            </svg>
        );
    }
    // key remounts the scene per card, resetting every ref and timer cleanly
    return <HeroScene key={idx} card={cards[idx % cards.length]}
        onDone={() => setIdx(i => i + 1)} />;
}

function HeroScene({ card, onDone }: { card: Card; onDone: () => void }) {
    const clipId = useId();
    const svgRef = useRef<SVGSVGElement>(null);
    const rootRef = useRef<SVGGElement>(null);
    const histRef = useRef<SVGPathElement>(null);
    const revealRef = useRef<SVGRectElement>(null);
    const tipRef = useRef<SVGGElement>(null);
    const labelRef = useRef<SVGTextElement>(null);

    const hist = card.sparkline!;
    const fcstEnd = card.fcst12To!;
    const all = [...hist, fcstEnd];
    const min = Math.min(...all), span = Math.max(...all) - min || 1;
    const histW = (HERO_W - HERO_PAD * 2) * 0.7;
    const x = (i: number) => HERO_PAD + (i / (hist.length - 1)) * histW;
    const y = (v: number) => HERO_PAD + (1 - (v - min) / span) * (HERO_H - HERO_PAD * 2);
    const histD = hist.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    const lastX = x(hist.length - 1), lastY = y(hist[hist.length - 1]);
    const fcstPct = card.price ? (fcstEnd / card.price - 1) * 100 : 0;

    useEffect(() => {
        const path = histRef.current!;
        const L = path.getTotalLength();

        // The svg stretches to fill its old footprint (preserveAspectRatio
        // "none"); counter-scale the card art so it stays undistorted.
        const box = svgRef.current!.getBoundingClientRect();
        const sx = box.width / HERO_W || 1, sy = box.height / HERO_H || 1;
        const k = Math.min(sx, sy);
        const artScale = `scale(${(k / sx).toFixed(4)} ${(k / sy).toFixed(4)})`;
        const placeTip = (px: number, py: number) =>
            tipRef.current?.setAttribute('transform', `translate(${px} ${py}) ${artScale}`);

        const finish = () => {   // fully-drawn state (also the reduced-motion state)
            path.style.strokeDasharray = 'none';
            placeTip(lastX, lastY);
            revealRef.current?.setAttribute('width', String(HERO_W));
            if (labelRef.current) labelRef.current.style.opacity = '1';
        };
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            finish();
            return;   // static: no cycling, no motion
        }

        path.style.strokeDasharray = `${L}`;
        path.style.strokeDashoffset = `${L}`;
        const ease = (t: number) => 1 - Math.pow(1 - t, 3);
        const t0 = performance.now();
        let raf = 0, leaving = false;
        let timeout: ReturnType<typeof setTimeout>;

        const frame = (now: number) => {
            const el = now - t0;
            const p1 = ease(Math.min(1, el / HERO.draw));
            path.style.strokeDashoffset = String(L * (1 - p1));
            const pt = path.getPointAtLength(L * p1);
            placeTip(pt.x, pt.y);
            if (el > HERO.draw) {
                const p2 = ease(Math.min(1, (el - HERO.draw) / HERO.fcst));
                revealRef.current?.setAttribute('width', String(lastX + (HERO_W - lastX) * p2));
                if (labelRef.current) labelRef.current.style.opacity = String(p2);
            }
            if (el > HERO.draw + HERO.fcst + HERO.hold) {
                if (!leaving) {
                    leaving = true;
                    rootRef.current?.classList.add('hero-scene--out');
                    timeout = setTimeout(onDone, HERO.out);
                }
                return;
            }
            raf = requestAnimationFrame(frame);
        };
        raf = requestAnimationFrame(frame);
        return () => { cancelAnimationFrame(raf); clearTimeout(timeout); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <svg ref={svgRef} className="hero__chart" viewBox={`0 0 ${HERO_W} ${HERO_H}`}
            preserveAspectRatio="none" aria-hidden="true">
            <defs>
                <clipPath id={`${clipId}-fcst`}>
                    <rect ref={revealRef} x="0" y="0" width="0" height={HERO_H} />
                </clipPath>
                <clipPath id={`${clipId}-art`}>
                    <rect x="-19" y="-27" width="38" height="54" rx="4" />
                </clipPath>
            </defs>
            <g ref={rootRef} className="hero-scene">
                <text className="hero-scene__name" x={HERO_PAD} y={HERO_PAD - 3}>
                    {card.name} · {currencyFormat(card.price)}
                </text>
                <path ref={histRef} className="hero__history" d={histD} fill="none" />
                <line
                    className="hero__forecast"
                    clipPath={`url(#${clipId}-fcst)`}
                    x1={lastX} y1={lastY}
                    x2={HERO_W - HERO_PAD} y2={y(fcstEnd)}
                />
                <text
                    ref={labelRef}
                    className="hero-scene__fcst"
                    x={HERO_W - HERO_PAD} y={y(fcstEnd) - 8}
                    textAnchor="end"
                    style={{ opacity: 0, fill: fcstPct >= 0 ? 'var(--up)' : 'var(--down)' }}
                >
                    {fcstPct >= 0 ? '+' : ''}{fcstPct.toFixed(0)}% 1Y FCST
                </text>
                <g ref={tipRef} transform={`translate(${x(0)}, ${y(hist[0])})`}>
                    <image
                        href={card.pictureUrl}
                        x="-19" y="-27" width="38" height="54"
                        clipPath={`url(#${clipId}-art)`}
                        preserveAspectRatio="xMidYMid slice"
                    />
                </g>
            </g>
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
                    <HeroChart movers={movers} />
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
