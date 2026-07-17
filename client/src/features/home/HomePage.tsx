import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { useUserInfoQuery } from "../account/accountApi";
import { currencyFormat, gameKey } from "../../lib/util";
import type { Card } from "../../app/models/card";
import Sparkline from "../../app/shared/components/Sparkline";
import PricePair from "../../app/shared/components/PricePair";
import ChangePill from "../../app/shared/components/ChangePill";
import { cardBackSrc, fallbackToCardBack } from "../../lib/cardImages";
import { usePageMeta } from "../../lib/usePageMeta";

// Hero animation: cycles through high-value movers. Each card's art rides the
// tip of its past-1Y price line as it draws itself; when the line reaches the
// present, the dotted forecast segment extends out; the scene holds, swipes
// away, and the next card begins. Decorative only.
const HERO = { draw: 3200, fcst: 900, hold: 1600, out: 500 };
const HERO_W = 560, HERO_H = 210, HERO_PAD = 16;

// Resolve once a card's art is actually decodable (SVG <image> has no onError
// fallback, so a failed load falls back to the game's card back here).
function preloadArt(card: Card) {
    return new Promise<string>(resolve => {
        const back = cardBackSrc(card.game, card.cardType);
        if (!card.pictureUrl) { resolve(back); return; }
        const img = new Image();
        img.onload = () => resolve(card.pictureUrl!);
        img.onerror = () => resolve(back);
        img.src = card.pictureUrl;
    });
}

// '1m' -> '1M', '6m' -> '6M', '12m' -> '1Y': label next to a mover's forecast.
const hzLabel = (h?: string) => (h === '1m' ? '1M' : h === '6m' ? '6M' : '1Y');
// A mover's headline forecast price: fcstTo at the card's own ranking horizon
// (the hero mixes categories; tiles rank on 1m), legacy 12m field as fallback.
const moverFcst = (m: Card) => m.fcstTo ?? m.fcst12To;

function HeroChart({ movers }: { movers?: Card[] }) {
    const cards = useMemo(() => {
        // Same value floor as the movers feed itself ($10); the sparkline just
        // needs enough points to trace a line worth watching.
        const ok = (movers ?? []).filter(m =>
            (m.price ?? 0) >= 10 && (m.sparkline?.length ?? 0) >= 4 && moverFcst(m) != null);
        // Round-robin across games (each game's pool shuffled) so consecutive
        // scenes cycle through a variety of games, not one game's whole list.
        const byGame = new Map<string, Card[]>();
        for (const m of [...ok].sort(() => Math.random() - 0.5)) {
            const g = gameKey(m.game);
            byGame.set(g, [...(byGame.get(g) ?? []), m]);
        }
        const pools = [...byGame.values()];
        const mixed: Card[] = [];
        for (let i = 0; pools.some(p => i < p.length); i++)
            for (const p of pools) if (i < p.length) mixed.push(p[i]);
        return mixed;
    }, [movers]);
    // A scene only mounts once its art is loaded: the first one waits on the
    // placeholder; later ones are gated by canLeave below, so the finished
    // scene sits at its end state until the next card's art is in.
    const [scene, setScene] = useState<{ i: number; art: string } | null>(null);
    const [nextArt, setNextArt] = useState<string | null>(null);

    useEffect(() => {   // first scene: wait for its art before playing
        if (!cards.length) { setScene(null); return; }
        let live = true;
        preloadArt(cards[0]).then(art => { if (live) setScene({ i: 0, art }); });
        return () => { live = false; };
    }, [cards]);

    const nextIdx = (scene?.i ?? 0) + 1;
    useEffect(() => {   // preload the NEXT card's art while this scene plays
        if (!scene || !cards.length) return;
        let live = true;
        setNextArt(null);
        preloadArt(cards[nextIdx % cards.length])
            .then(art => { if (live) setNextArt(art); });
        return () => { live = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scene?.i, cards]);

    if (!cards.length || !scene) {
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
    // key remounts the scene per card, resetting every ref and timer cleanly.
    // canLeave: the swipe-out is held until the next card's art has loaded;
    // onDone advances with that art, so the new scene starts instantly.
    return <HeroScene key={scene.i} card={cards[scene.i % cards.length]}
        artHref={scene.art}
        canLeave={nextArt != null}
        onDone={() => setScene({ i: nextIdx, art: nextArt! })} />;
}

function HeroScene({ card, artHref, canLeave, onDone }: {
    card: Card; artHref: string; canLeave: boolean; onDone: () => void;
}) {
    const clipId = useId();
    // The rAF effect runs once at mount; these props change while the scene
    // plays (next art finishing its load), so the loop reads them via refs.
    const canLeaveRef = useRef(canLeave);
    const onDoneRef = useRef(onDone);
    useEffect(() => { canLeaveRef.current = canLeave; onDoneRef.current = onDone; });
    const svgRef = useRef<SVGSVGElement>(null);
    const rootRef = useRef<SVGGElement>(null);
    const histRef = useRef<SVGPathElement>(null);
    const revealRef = useRef<SVGRectElement>(null);
    const tipRef = useRef<SVGGElement>(null);
    const nameRef = useRef<SVGTextElement>(null);
    const labelRef = useRef<SVGTextElement>(null);

    const hist = card.sparkline!;
    const fcstEnd = moverFcst(card)!;
    const all = [...hist, fcstEnd];
    const min = Math.min(...all), span = Math.max(...all) - min || 1;
    // Insets sized to the card art (108 tall / 76 wide, centered on the line
    // tip) so it can never be clipped: 54px of vertical clearance both ways,
    // and the line starts far enough in that the art clears the left edge.
    // PAD_TOP additionally keeps the art's top edge (tip y − 54) below the
    // name label band at the top-left (baseline y=13).
    const PAD_TOP = 74, PAD_BOT = 58, PAD_LEFT = 44;
    const histW = (HERO_W - PAD_LEFT - HERO_PAD) * 0.7;
    const x = (i: number) => PAD_LEFT + (i / (hist.length - 1)) * histW;
    const y = (v: number) => PAD_TOP + (1 - (v - min) / span) * (HERO_H - PAD_TOP - PAD_BOT);
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

        // Text must not stretch OR grow with the panel — pin it to a static
        // pixel size by fully undoing the container scale (net 1:1), anchored
        // at each label's own corner so it stays put.
        const invX = (1 / sx).toFixed(4), invY = (1 / sy).toFixed(4);
        const pinText = (el: SVGTextElement | null, ax: number, ay: number) =>
            el?.setAttribute('transform',
                `translate(${ax} ${ay}) scale(${invX} ${invY}) translate(${-ax} ${-ay})`);
        pinText(nameRef.current, HERO_PAD, HERO_PAD - 3);
        pinText(labelRef.current, HERO_W - HERO_PAD, HERO_H - 10);

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
                // Hold at the finished state until the next card's art has
                // loaded (canLeave); only then swipe out and hand over.
                if (canLeaveRef.current && !leaving) {
                    leaving = true;
                    rootRef.current?.classList.add('hero-scene--out');
                    timeout = setTimeout(() => onDoneRef.current(), HERO.out);
                    return;
                }
                if (leaving) return;
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
                    <rect x="-38" y="-54" width="76" height="108" rx="7" />
                </clipPath>
            </defs>
            <g ref={rootRef} className="hero-scene">
                <text ref={nameRef} className="hero-scene__name" x={HERO_PAD} y={HERO_PAD - 3}>
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
                    x={HERO_W - HERO_PAD} y={HERO_H - 10}
                    textAnchor="end"
                    style={{ opacity: 0, fill: fcstPct >= 0 ? 'var(--up)' : 'var(--down)' }}
                >
                    {fcstPct >= 0 ? '+' : ''}{fcstPct.toFixed(0)}% {hzLabel(card.fcstHorizon)} FCST
                </text>
                <g ref={tipRef} transform={`translate(${x(0)}, ${y(hist[0])})`}>
                    <image
                        href={artHref}
                        x="-38" y="-54" width="76" height="108"
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
                        <PricePair price={mover.price} forecast={moverFcst(mover)}
                            horizon={hzLabel(mover.fcstHorizon)} />
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
    { n: '1', title: 'Browse', text: 'Every card across six TCGs (One Piece, Pokémon, Yu-Gi-Oh!, Lorcana, Digimon, Gundam), with ungraded and graded price tiers and full price history.' },
    { n: '2', title: 'Forecast', text: '1 month, 6 month, and 1 year ML price predictions with confidence bands and plain-English reasoning.' },
    { n: '3', title: 'Track', text: 'A brokerage-style portfolio with P/L that benchmarks your collection against the S&P 500.' },
    { n: '4', title: 'Stay ahead', text: 'Price and forecast alerts per card and condition, plus a market report every Friday.' },
];

export default function HomePage() {
    usePageMeta(undefined,
        "Trading card price predictions from a machine-learned model: market movers, graded history, and portfolio tracking across six TCGs.");
    const { data: movers } = useFetchMoversQuery({ count: 12, horizon: '1m' });
    // The hero graph cycles a MIX of forecast categories (1M/6M/1Y) across
    // games; the tiles below stay on the 1-month ranking.
    const { data: heroMovers } = useFetchMoversQuery({ count: 12, horizon: 'mix' });
    const { data: user } = useUserInfoQuery();
    const tiles = movers?.slice(0, 4) ?? [];

    return (
        <>
            <section className="hero subgrid full-span">
                <div className="hero__copy">
                    <h1 className="hero__title">The stock market for trading cards.</h1>
                    <p className="hero__sub">
                        Machine-learned price predictions for cards across six TCGs, with
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
                    <HeroChart movers={heroMovers} />
                </div>
            </section>

            {tiles.length > 0 && (
                <section className="full-span subgrid">
                    <h2 className="home__heading full-span">
                        Top movers <span className="est-note">· 1 month model forecast</span>
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
