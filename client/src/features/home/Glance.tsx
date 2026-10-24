import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import type { User } from "../../app/models/user";
import { currencyFormat } from "../../lib/util";
import { gameColor } from "../../lib/games";
import ChangePill from "../../app/shared/components/ChangePill";
import { useFetchPortfolioSummaryQuery } from "../watchlist/watchlistApi";
import { useFetchHomeStatsQuery } from "./homeApi";

// Home infographics (2026-10-23/24): the collection block (section 02, its
// own row) and the three model-performance panels (section 03), in place of
// the old how-it-works boxes. Each panel reveals once it scrolls into view —
// bars grow, rings draw, numbers count up — and all of it collapses to the
// final state under prefers-reduced-motion.

const reducedMotion = () =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// True once the element has entered the viewport (never flips back).
export function useInView<T extends HTMLElement>() {
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
export function useCountUp(target: number, active: boolean, ms = 1200) {
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

export const compact = (n: number) =>
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

// "+$212.00" / "−$48.10" for the P/L cells.
const signedUsd = (n: number) => (n < 0 ? '−' : '+') + currencyFormat(Math.abs(n));

// Log-return error → the percentage a reader understands ("±12%").
export const pct = (logRet: number) => (Math.exp(Math.abs(logRet)) - 1) * 100;

// ----- 1. Portfolio (signed in) / Track your collection (signed out) -------
// Its own full-width row, taller (user request 2026-10-24): ring + legend,
// a six-stat grid under the headline value, and the value-over-time line
// against the S&P what-if. Signed out, every figure is a labelled sample.

// Value line (brand) with the "same $ in the S&P 500" line (muted, dashed).
function ValueLine({ series, bench, inView }: { series: number[]; bench?: number[]; inView: boolean }) {
    const W = 260, H = 96, P = 4;
    const all = [...series, ...(bench ?? [])];
    const min = Math.min(...all), span = Math.max(...all) - min || 1;
    const path = (pts: number[]) => pts.map((v, i) =>
        `${i ? 'L' : 'M'}${(P + (i / (pts.length - 1)) * (W - 2 * P)).toFixed(1)} ${(P + (1 - (v - min) / span) * (H - 2 * P)).toFixed(1)}`).join(' ');
    const up = series[series.length - 1] >= series[0];
    return (
        <svg className="glance__line" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true" data-in={inView ? 1 : 0}>
            {bench && bench.length > 1 && <path className="glance__line-bench" d={path(bench)} />}
            <path className={`glance__line-value${up ? '' : ' glance__line-value--down'}`} d={path(series)} />
        </svg>
    );
}

// Evenly thin a daily series to at most `n` points (keeps the last one).
function thin(values: number[], n = 60) {
    if (values.length <= n) return values;
    const step = (values.length - 1) / (n - 1);
    return Array.from({ length: n }, (_, i) => values[Math.round(i * step)]);
}

const SAMPLE = {
    value: 4820, month: 212, monthPct: 4.6, paid: 3680, plUsd: 1140, plPct: 31.0, copies: 142, packs: 23,
    best: 'Charizard ex', bestPct: 38, spx: 4215,
    series: [3680, 3740, 3700, 3810, 3900, 3860, 3980, 4120, 4050, 4210, 4330, 4290, 4460, 4610, 4580, 4700, 4820],
    bench: [3680, 3700, 3720, 3750, 3790, 3770, 3830, 3880, 3900, 3950, 3990, 4020, 4080, 4130, 4150, 4190, 4215],
};

function PortfolioPanel({ user }: { user?: User | null }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data: summary } = useFetchPortfolioSummaryQuery({ includePacks: true }, { skip: !user });
    const has = !!user && !!summary && summary.copies > 0;
    const value = useCountUp(has ? summary!.totalValue : SAMPLE.value, inView);
    const slices = has
        ? (summary!.allocation ?? []).slice(0, 6).map(a => ({ label: a.label, pct: a.pct, color: gameColor(a.label) }))
        : [{ label: 'Pokémon', pct: 38, color: gameColor('pokemon') },
           { label: 'One Piece', pct: 27, color: gameColor('onepiece') },
           { label: 'Magic', pct: 21, color: gameColor('magic') },
           { label: 'Lorcana', pct: 14, color: gameColor('lorcana') }];
    // The six stats. Real figures when the member has cards; the sample otherwise.
    const series = has ? thin((summary!.series ?? []).map(p => p.value)) : SAMPLE.series;
    const bench = has ? thin((summary!.benchmark ?? []).map(p => p.value)) : SAMPLE.bench;
    const spxEnd = bench.length ? bench[bench.length - 1] : null;
    const vsSpx = spxEnd && series.length ? (series[series.length - 1] / spxEnd - 1) * 100 : null;
    const stats: { label: string; value: string; pill?: { value: number; unit?: 'usd' | 'percent' } ; note?: string }[] = has
        ? [
            { label: 'This month', value: summary!.monthChangeUsd != null ? signedUsd(summary!.monthChangeUsd) : '—',
              pill: summary!.monthChangePct != null ? { value: summary!.monthChangePct } : undefined },
            { label: 'All-time P/L', value: summary!.allTime ? signedUsd(summary!.allTime.plUsd) : '—',
              pill: summary!.allTime ? { value: summary!.allTime.plPct } : undefined },
            { label: 'Cost basis', value: summary!.allTime ? currencyFormat(summary!.allTime.paid) : '—' },
            { label: 'Cards', value: summary!.copies.toLocaleString(),
              note: summary!.packCopies ? `${summary!.packCopies.toLocaleString()} from packs` : undefined },
            { label: 'Best performer', value: summary!.best?.name ?? '—',
              pill: summary!.best ? { value: summary!.best.pct } : undefined },
            { label: 'vs S&P 500', value: vsSpx != null ? `${vsSpx >= 0 ? '+' : ''}${vsSpx.toFixed(1)}%` : '—',
              note: 'same money in the index' },
          ]
        : [
            { label: 'This month', value: signedUsd(SAMPLE.month), pill: { value: SAMPLE.monthPct } },
            { label: 'All-time P/L', value: signedUsd(SAMPLE.plUsd), pill: { value: SAMPLE.plPct } },
            { label: 'Cost basis', value: currencyFormat(SAMPLE.paid) },
            { label: 'Cards', value: String(SAMPLE.copies), note: `${SAMPLE.packs} from packs` },
            { label: 'Best performer', value: SAMPLE.best, pill: { value: SAMPLE.bestPct } },
            { label: 'vs S&P 500', value: `+${((SAMPLE.value / SAMPLE.spx - 1) * 100).toFixed(1)}%`, note: 'same money in the index' },
          ];
    return (
        <div ref={ref} className="panel glance__panel glance__panel--wide glance__panel--tall" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">{has ? 'Your portfolio' : 'Track your collection'}</span>
                <span className="mono">{has ? `${summary!.copies.toLocaleString()} cards · live` : 'sample figures'}</span>
            </div>
            <div className="glance__wide">
                <div className="glance__wide-alloc">
                    <Ring slices={slices} size={120} stroke={14} inView={inView} />
                    <ul className="glance__legend">
                        {slices.map(s => (
                            <li key={s.label}>
                                <span className="glance__dot" style={{ background: s.color }} />{s.label}
                                <span className="mono"> {Math.round(s.pct)}%</span>
                            </li>
                        ))}
                    </ul>
                </div>
                <div className="glance__wide-main">
                    <div className="glance__big">{currencyFormat(value)}</div>
                    <div className="mono glance__big-label">{has ? 'collection value today' : 'sample collection value'}</div>
                    <dl className="glance__stats">
                        {stats.map(st => (
                            <div className="glance__stat-cell" key={st.label}>
                                <dt className="mono">{st.label}</dt>
                                <dd>
                                    <span className="glance__stat-val">{st.value}</span>
                                    {st.pill && <ChangePill value={st.pill.value} unit={st.pill.unit} />}
                                    {st.note && <span className="glance__stat-note">{st.note}</span>}
                                </dd>
                            </div>
                        ))}
                    </dl>
                </div>
                <div className="glance__wide-chart">
                    <div className="mono glance__chart-title">
                        <span><span className="glance__dot glance__dot--brand" />value</span>
                        <span><span className="glance__dot glance__dot--bench" />same $ in S&amp;P 500</span>
                    </div>
                    {series.length > 1
                        ? <ValueLine series={series} bench={bench} inView={inView} />
                        : <div className="glance__text">The value line starts after your first day of prices.</div>}
                    <div className="glance__foot glance__foot--chart">
                        {has
                            ? <Link to="/portfolio">Open your portfolio →</Link>
                            : user
                                ? <Link to="/portfolio">Add your first card →</Link>
                                : <Link className="btn btn--sm" to="/register">Start tracking free</Link>}
                    </div>
                </div>
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
                {/* Until the stats arrive the figures read as a dash, never as
                    "0 forecasts graded" (the API's first call after a restart
                    takes a few seconds while it primes its cache). */}
                <div className="glance__stat">
                    <div className="glance__big">{data ? compact(graded) : '—'}</div>
                    <div className="mono">forecasts graded</div>
                </div>
                <div className="glance__stat">
                    <div className="glance__big glance__big--up">{data ? `${hit.toFixed(1)}%` : '—'}</div>
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
                Every forecast is saved and graded once it lands.{' '}
                <Link to="/about">How the forecasts work →</Link>
            </div>
        </div>
    );
}

// ----- 3. How far off: the typical miss and its lean, per game ---------------
function MissPanel() {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data } = useFetchHomeStatsQuery();
    const miss = useCountUp(data ? pct(data.mae) : 0, inView && !!data);
    const lean = useCountUp(data ? pct(data.bias) : 0, inView && !!data);
    const hot = (data?.bias ?? 0) > 0;
    const worst = Math.max(...(data?.miss ?? []).map(g => pct(g.mae)), 1);
    return (
        <div ref={ref} className="panel glance__panel" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">How far off</span>
                <span className="mono">1 month · near mint</span>
            </div>
            <div className="glance__row">
                <div className="glance__stat">
                    <div className="glance__big">{data ? `±${miss.toFixed(1)}%` : '—'}</div>
                    <div className="mono">typical miss</div>
                </div>
                <div className="glance__stat">
                    <div className={`glance__big ${hot ? 'glance__big--down' : 'glance__big--up'}`}>
                        {data ? `${lean.toFixed(1)}% ${hot ? 'hot' : 'cold'}` : '—'}
                    </div>
                    <div className="mono">{hot ? 'calls run above the landing' : 'calls run below the landing'}</div>
                </div>
            </div>
            <ul className="glance__bars">
                {(data?.miss ?? []).map((g, i) => (
                    <li key={g.game} className="glance__barrow" style={{ '--i': i } as CSSProperties}
                        title={`${g.label}: typical miss ±${pct(g.mae).toFixed(1)}%, runs ${pct(g.bias).toFixed(1)}% ${g.bias > 0 ? 'hot' : 'cold'} over ${g.graded.toLocaleString()} graded`}>
                        <span className="glance__barlabel">{g.label}</span>
                        <span className="glance__track">
                            <span className="glance__fill"
                                style={{ '--w': pct(g.mae) / worst, '--c': gameColor(g.game) } as CSSProperties} />
                        </span>
                        <span className="mono glance__barval">±{Math.round(pct(g.mae))}%</span>
                    </li>
                ))}
            </ul>
            <div className="glance__foot">
                Average gap between the model's 1-month call and where the price landed.
                Shorter is better; hot means the calls run high, cold means they run low.
            </div>
        </div>
    );
}

// ----- 4. Where the model leans ----------------------------------------------
function LeanPanel() {
    const { ref, inView } = useInView<HTMLDivElement>();
    const { data } = useFetchHomeStatsQuery();
    return (
        <div ref={ref} className="panel glance__panel" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">Where the model leans</span>
                <span className="mono">next month</span>
            </div>
            {!data && <div className="glance__text">Loading the model's calls…</div>}
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

// Section 02: the collection block on its own row.
export function CollectionGlance({ user }: { user?: User | null }) {
    return (
        <div className="glance subgrid">
            <PortfolioPanel user={user} />
        </div>
    );
}

// Section 03: the three model panels.
export default function Glance() {
    return (
        <div className="glance subgrid">
            <TrackRecordPanel />
            <MissPanel />
            <LeanPanel />
        </div>
    );
}
