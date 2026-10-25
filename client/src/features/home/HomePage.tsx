import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { useUserInfoQuery } from "../account/accountApi";
import { currencyFormat, gameKey } from "../../lib/util";
import type { Card } from "../../app/models/card";
import Sparkline from "../../app/shared/components/Sparkline";
import PricePair from "../../app/shared/components/PricePair";
import ChangePill from "../../app/shared/components/ChangePill";
import Glance, { CollectionGlance } from "./Glance";
import Carousel from "./Carousel";
import { GAMES } from "../../lib/games";
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
const hzLabel = (h?: string) => (h === '1m' ? '1M' : h === '6m' ? '6M' : h === 'launch1m' ? 'EARLY' : '1Y');
// Text that rolls when it changes: the old line slides up and fades out
// while the new one rises in from below (the featured-card band). At most
// two lines exist at once; the outgoing one is dropped when its animation
// ends. Under prefers-reduced-motion the CSS swaps instantly.
function RollingText({ text, className = '' }: { text: string; className?: string }) {
    const [items, setItems] = useState(() => [{ id: 0, text, out: false }]);
    const nextId = useRef(1);
    useEffect(() => {
        setItems(prev => {
            if (prev.find(p => !p.out)?.text === text) return prev;
            return [
                ...prev.filter(p => !p.out).map(p => ({ ...p, out: true })),
                { id: nextId.current++, text, out: false },
            ];
        });
    }, [text]);
    return (
        <span className={`roll ${className}`.trim()}>
            {items.map(it => (
                <span key={it.id} className={`roll__item${it.out ? ' roll__item--out' : ''}`}
                    aria-hidden={it.out || undefined}
                    onAnimationEnd={() => it.out && setItems(prev => prev.filter(p => p.id !== it.id))}>
                    {it.text}
                </span>
            ))}
        </span>
    );
}

// Spelled-out horizon for the featured-card band: "6 month forecast".
const hzLong = (h?: string) =>
    h === '1m' ? '1 month' : h === '6m' ? '6 month' : h === 'launch1m' ? 'Early' : '1 year';
// A mover's headline forecast price: fcstTo at the card's own ranking horizon
// (the hero mixes categories; tiles rank on 1m), legacy 12m field as fallback.
const moverFcst = (m: Card) => m.fcstTo ?? m.fcst12To;

const shuffle = <T,>(xs: T[]) => {
    const out = [...xs];
    for (let i = out.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
};

// The hero's running order (user request 2026-10-24): the feed carries every
// game at every forecast length, and the scenes rotate 1 month → 6 month →
// 1 year so the band keeps changing. Games are shuffled once per mount;
// each horizon's list walks that same game order from a different starting
// point (a third of the way round per horizon), so back-to-back scenes are
// never the same game either. EARLY calls stand in for a game's 1m pick.
const HERO_HORIZONS = ['1m', '6m', '12m'];
function heroOrder(cards: Card[]): Card[] {
    const hzOf = (c: Card) =>
        c.fcstHorizon === '1m' || c.fcstHorizon === 'launch1m' ? '1m' : c.fcstHorizon === '6m' ? '6m' : '12m';
    const games = shuffle([...new Set(cards.map(c => gameKey(c.game)))]);
    const lanes = HERO_HORIZONS.map((h, k) => {
        const start = Math.round(k * games.length / HERO_HORIZONS.length);
        const order = [...games.slice(start), ...games.slice(0, start)];
        const byGame = new Map(order.map(g =>
            [g, shuffle(cards.filter(c => gameKey(c.game) === g && hzOf(c) === h))]));
        const lane: Card[] = [];
        for (let round = 0; ; round++) {   // one card per game per round
            const row = order.map(g => byGame.get(g)?.[round]).filter((c): c is Card => !!c);
            if (!row.length) break;
            lane.push(...row);
        }
        return lane;
    });
    const out: Card[] = [];
    for (let i = 0; lanes.some(l => i < l.length); i++)
        for (const lane of lanes) if (lane[i]) out.push(lane[i]);
    return out;
}

// onCard reports the card each scene features (null while the placeholder
// line shows) so the panel's band can name the horizon on screen.
function HeroChart({ movers, onCard }: { movers?: Card[]; onCard?: (c: Card | null) => void }) {
    const cards = useMemo(() => {
        // The feed already applies the showcase bar (value floor, fresh price,
        // art); the sparkline just needs two points to trace a line. No
        // extra floor here (price or history depth) — it would silently drop
        // a game's only pick at some horizon and break the every-game promise
        // (young cards carry only a couple of monthly points).
        const ok = (movers ?? []).filter(m =>
            (m.sparkline?.length ?? 0) >= 2 && moverFcst(m) != null);
        return heroOrder(ok);   // fresh order every mount
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

    const shown = scene && cards.length ? cards[scene.i % cards.length] : null;
    useEffect(() => { onCard?.(shown); }, [shown, onCard]);

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
    // A call that rounds to 0% reads neutral, not as a green "+0%" — the
    // model's flat segments (a game's whole 6m, say) feature as "0%".
    const flat = Math.abs(fcstPct) < 0.5;

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
        // The whole scene links to the card it's currently drawing.
        <Link to={`/catalog/${gameKey(card.game)}/${card.id}`} className="hero__chart-link"
            aria-label={`View ${card.name}`} title={`View ${card.name}`}>
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
                    style={{ opacity: 0, fill: flat ? 'var(--text-muted)' : fcstPct > 0 ? 'var(--up)' : 'var(--down)' }}
                >
                    {flat ? '0' : `${fcstPct > 0 ? '+' : ''}${fcstPct.toFixed(0)}`}% {card.fcstHorizon === 'launch1m' ? 'EARLY CALL' : `NEXT ${hzLabel(card.fcstHorizon)}`}
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
        </Link>
    );
}

function MoverTile({ mover, index = 0 }: { mover: Card; index?: number }) {
    const path = `/catalog/${gameKey(mover.game)}/${mover.id}`;
    // Top accent follows the forecast's direction; --i staggers the entrance.
    const fcst = moverFcst(mover);
    const dir = fcst != null && mover.price
        ? (fcst >= mover.price ? ' mover--up' : ' mover--down') : '';
    return (
        <Link to={path} className={`mover panel${dir}`}
            style={{ '--i': index } as CSSProperties}>
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
                                <span className="mono">LAST {(mover.trendPeriod ?? '1y').toUpperCase()}</span>
                            </span>
                        )}
                        <Sparkline points={mover.sparkline} />
                    </span>
                </div>
            </div>
        </Link>
    );
}

export default function HomePage() {
    usePageMeta(undefined,
        "Trading card price predictions powered by AI. See what Magic, Pokémon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, Gundam, and Star Wars Unlimited cards will be worth in a month, six months, or a year.");
    // count=24 so the per-game guarantee (gainer+loser per game) fits for all
    // eight games; the dedupe below then keeps one tile per game. Tiles show
    // the past six months (trend) next to the 1-month forecast they rank on.
    const { data: movers } = useFetchMoversQuery({ count: 24, horizon: '1m', trend: '6m' });
    // The hero graph cycles every forecast length (1M/6M/1Y) with two cards
    // per game AT EACH length — its strongest gainer and loser there — in a
    // horizon-rotating order built client-side (heroOrder); the tiles below
    // stay on the 1-month ranking.
    const { data: heroMovers } = useFetchMoversQuery({ horizon: 'mix', perGame: 2 });
    const { data: user } = useUserInfoQuery();
    // The card the hero scene is featuring right now; the band follows it.
    const [heroCard, setHeroCard] = useState<Card | null>(null);
    // One tile per game (user request 2026-10-23): each game's strongest
    // mover, in ranking order. A game with no showable mover simply has no
    // slide — the feed's per-game guarantee (with its early-tier fallback)
    // decides which games appear.
    const tiles = useMemo(() => {
        const seen = new Set<string>();
        const picks: Card[] = [];
        for (const m of movers ?? []) {
            if (seen.has(gameKey(m.game))) continue;
            seen.add(gameKey(m.game));
            picks.push(m);
            if (picks.length === GAMES.length) break;
        }
        return picks;
    }, [movers]);

    return (
        <>
            <section className="hero subgrid full-span hero-field">
                <div className="hero__copy">
                    <h1 className="hero__title">
                        Know your cards' worth: yesterday, today,{' '}
                        <span className="hero__accent">tomorrow.</span>
                    </h1>
                    <p className="hero__sub">
                        CardStock watches the market for twelve card games and predicts where any card's
                        price is headed a month, six months and a year out. Raw or graded, with
                        full price history and a tracker for everything you own.
                    </p>
                    <div className="btn-group">
                        {user
                            ? <Link className="btn" to="/portfolio">Open your collection</Link>
                            : <Link className="btn" to="/register">Start tracking free</Link>}
                        <Link className="btn btn--outline" to="/catalog">Look up a card</Link>
                    </div>
                </div>
                <div className="hero__panel">
                    <div className="band">
                        <span className="band__title">Featured cards</span>
                        <RollingText className="mono"
                            text={heroCard ? `${hzLong(heroCard.fcstHorizon)} forecast · live` : 'model forecast · live'} />
                    </div>
                    <HeroChart movers={heroMovers} onCard={setHeroCard} />
                </div>
            </section>

            {tiles.length > 0 && (
                <section className="full-span subgrid">
                    <span className="kicker home__kicker full-span">01 · Market</span>
                    <h2 className="home__heading full-span">
                        Biggest expected moves <span className="est-note">· next month · one card per game</span>
                    </h2>
                    <Carousel className="movers" label="Top movers, one card per game"
                        slides={tiles.map((m, i) => ({
                            key: `${m.game}-${m.id}`,
                            node: <MoverTile mover={m} index={i} />,
                        }))} />
                </section>
            )}

            <section className="full-span subgrid">
                <span className="kicker home__kicker full-span">02 · Your collection</span>
                <CollectionGlance user={user} />
            </section>

            <section className="full-span subgrid">
                <span className="kicker home__kicker full-span">03 · Our track record</span>
                <Glance />
            </section>
        </>
    );
}
