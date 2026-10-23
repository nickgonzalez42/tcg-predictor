import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import type { User } from "../../app/models/user";
import { currencyFormat } from "../../lib/util";
import { gameColor } from "../../lib/games";
import ChangePill from "../../app/shared/components/ChangePill";
import { useFetchPortfolioSummaryQuery } from "../watchlist/watchlistApi";
import { useFetchHomeStatsQuery } from "./homeApi";

// "At a glance" (2026-10-23): three animated infographics in place of the
// old how-it-works boxes. Each panel reveals once it scrolls into view —
// bars grow, rings draw, numbers count up — and all of it collapses to the
// final state under prefers-reduced-motion.

const reducedMotion = () =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// True once the element has entered the viewport (never flips back).
function useInView<T extends HTMLElement>() {
    const ref = useRef<T>(null);
    const [inView, setInView] = useState(() => reducedMotion());
    useEffect(() => {
        const el = ref.current;
        if (!el || inView) return;
        if (!('IntersectionObserver' in window)) { setInView(true); return; }
        const io = new IntersectionObserver(entries => {
            if (entries.some(e => e.isIntersecting)) { setInView(true); io.disconnect(); }
        }, { threshold: 0.25 });
        io.observe(el);
        return () => io.disconnect();
    }, [inView]);
    return { ref, inView };
}

// Eases a number from 0 to `target` over `ms` once `active`.
function useCountUp(target: number, active: boolean, ms = 1200) {
    const [value, setValue] = useState(0);
    useEffect(() => {
        if (!active) return;
        if (reducedMotion()) { setValue(target); return; }
        let raf = 0;
        const t0 = performance.now();
        const tick = (t: number) => {
            const p = Math.min(1, (t - t0) / ms);
            const eased = 1 - Math.pow(1 - p, 3);
            setValue(target * eased);
            if (p < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [target, active, ms]);
    return value;
}

const compact = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${Math.round(n)}`;

// Allocation ring: one arc per slice, drawn in sequence once in view.
function Ring({ slices, size = 104, stroke = 13, inView }: {
    slices: { label: string; pct: number; color: string }[]; size?: number; stroke?: number; inView: boolean;
}) {
    const r = (size - stroke) / 2;
    const circ = 2 * Math.PI * r;
    const total = slices.reduce((s, x) => s + x.pct, 0) || 1;
    let offset = 0;
    return (
        <svg className="glance__ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
            <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={stroke} />
            {slices.map((s, i) => {
                const len = (s.pct / total) * circ;
                const el = (
                    <circle key={s.label} cx={size / 2} cy={size / 2} r={r} fill="none"
                        stroke={s.color} strokeWidth={stroke}
                        strokeDasharray={`${len} ${circ - len}`}
                        strokeDashoffset={inView ? -offset : circ}
                        style={{ '--i': i } as CSSProperties}
                        transform={`rotate(-90 ${size / 2} ${size / 2})`} />
                );
                offset += len;
                return el;
            })}
        </svg>
    );
}

// ----- 1. Portfolio (signed in) / Track your collection (signed out) -------
function PortfolioPanel({ user }: { user?: User | null }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data: summary } = useFetchPortfolioSummaryQuery({ includePacks: true }, { skip: !user });
    const has = !!user && !!summary && summary.copies > 0;
    const value = useCountUp(has ? summary!.totalValue : 0, inView && has);
    const slices = has
        ? (summary!.allocation ?? []).slice(0, 6).map(a => ({ label: a.label, pct: a.pct, color: gameColor(a.label) }))
        : [{ label: 'Pokémon', pct: 38, color: gameColor('pokemon') },
           { label: 'One Piece', pct: 27, color: gameColor('onepiece') },
           { label: 'Magic', pct: 21, color: gameColor('magic') },
           { label: 'Lorcana', pct: 14, color: gameColor('lorcana') }];
    return (
        <div ref={ref} className="panel glance__panel" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">{has ? 'Your portfolio' : 'Track your collection'}</span>
                <span className="mono">{has ? `${summary!.copies} cards` : 'sample'}</span>
            </div>
            <div className="glance__row">
                <Ring slices={slices} inView={inView} />
                <div className="glance__col">
                    {has ? (
                        <>
                            <div className="glance__big">{currencyFormat(value)}</div>
                            <div className="glance__pills">
                                {summary!.monthChangeUsd != null && (
                                    <ChangePill value={summary!.monthChangeUsd} unit="usd" suffix="this month" />
                                )}
                                {summary!.allTime && <ChangePill value={summary!.allTime.plPct} suffix="vs cost" />}
                            </div>
                        </>
                    ) : (
                        <p className="glance__text">
                            Add your cards and see their value, P/L by condition, allocation by
                            game, and how the collection stacks up against the S&amp;P 500.
                        </p>
                    )}
                    <ul className="glance__legend">
                        {slices.map(s => (
                            <li key={s.label}>
                                <span className="glance__dot" style={{ background: s.color }} />{s.label}
                                <span className="mono"> {Math.round(s.pct)}%</span>
                            </li>
                        ))}
                    </ul>
                </div>
            </div>
            <div className="glance__foot">
                {has
                    ? <Link to="/portfolio">Open your portfolio →</Link>
                    : user
                        ? <Link to="/portfolio">Add your first card →</Link>
                        : <Link className="btn btn--sm" to="/register">Start tracking free</Link>}
            </div>
        </div>
    );
}

// ----- 2. Model track record --------------------------------------------------
function TrackRecordPanel() {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data } = useFetchHomeStatsQuery();
    const graded = useCountUp(data?.graded ?? 0, inView && !!data);
    const hit = useCountUp((data?.bandHit ?? 0) * 100, inView && !!data);
    return (
        <div ref={ref} className="panel glance__panel" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">Model track record</span>
                <span className="mono">1 month · near mint</span>
            </div>
            <div className="glance__row">
                <div className="glance__stat">
                    <div className="glance__big">{compact(graded)}</div>
                    <div className="mono">forecasts graded</div>
                </div>
                <div className="glance__stat">
                    <div className="glance__big glance__big--up">{hit.toFixed(1)}%</div>
                    <div className="mono">landed inside the model's 80% range</div>
                </div>
            </div>
            <ul className="glance__bars">
                {(data?.byGame ?? []).map((g, i) => (
                    <li key={g.game} className="glance__barrow" style={{ '--i': i } as CSSProperties}>
                        <span className="glance__barlabel">{g.label}</span>
                        <span className="glance__track">
                            <span className="glance__fill"
                                style={{ '--w': g.bandHit, '--c': gameColor(g.game) } as CSSProperties} />
                        </span>
                        <span className="mono glance__barval">{Math.round(g.bandHit * 100)}%</span>
                    </li>
                ))}
            </ul>
            <div className="glance__foot">
                Every forecast is saved and graded 28 days later.{' '}
                <Link to="/about">How the forecasts work →</Link>
            </div>
        </div>
    );
}

// ----- 3. Where the model leans ----------------------------------------------
function LeanPanel() {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data } = useFetchHomeStatsQuery();
    return (
        <div ref={ref} className="panel glance__panel" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">Where the model leans</span>
                <span className="mono">next month</span>
            </div>
            <ul className="glance__bars">
                {(data?.lean ?? []).map((g, i) => {
                    const n = Math.max(1, g.up + g.flat + g.down);
                    return (
                        <li key={g.game} className="glance__barrow" style={{ '--i': i } as CSSProperties}
                            title={`${g.label}: ${g.up.toLocaleString()} up · ${g.flat.toLocaleString()} unchanged · ${g.down.toLocaleString()} down`}>
                            <span className="glance__barlabel">{g.label}</span>
                            <span className="glance__stack">
                                <span className="glance__seg glance__seg--up" style={{ '--f': g.up / n } as CSSProperties} />
                                <span className="glance__seg glance__seg--flat" style={{ '--f': g.flat / n } as CSSProperties} />
                                <span className="glance__seg glance__seg--down" style={{ '--f': g.down / n } as CSSProperties} />
                            </span>
                            <span className="mono glance__barval glance__barval--up">{Math.round(100 * g.up / n)}%</span>
                        </li>
                    );
                })}
            </ul>
            <div className="glance__legend glance__legend--row">
                <span><span className="glance__dot glance__dot--up" />up</span>
                <span><span className="glance__dot glance__dot--flat" />unchanged</span>
                <span><span className="glance__dot glance__dot--down" />down</span>
            </div>
            <div className="glance__foot">
                Share of each game's cards the 1-month model has rising, flat, or falling.
                Flat means the call was damped to today's price.
            </div>
        </div>
    );
}

export default function Glance({ user }: { user?: User | null }) {
    return (
        <div className="glance subgrid">
            <PortfolioPanel user={user} />
            <TrackRecordPanel />
            <LeanPanel />
        </div>
    );
}
