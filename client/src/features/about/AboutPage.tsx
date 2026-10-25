import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";
import { gameColor } from "../../lib/games";
import { useFetchScorecardQuery, type ModelScore, type WeekScore } from "../home/homeApi";
import { useInView, useCountUp, compact, pct } from "../home/Glance";

// About (2026-10-24): infographics of how every model has actually done.
// Every forecast is archived the day it is issued and graded when its date
// arrives; the numbers here are that whole record, misses included, from
// GET home/scorecard. Model = one (game, condition tier, horizon).

const TIERS: { key: string; label: string }[] = [
    { key: 'ungraded', label: 'Near Mint' },
    { key: 'grade7', label: 'Grade 7' },
    { key: 'grade8', label: 'Grade 8' },
    { key: 'grade9', label: 'Grade 9' },
    { key: 'grade95', label: 'Grade 9.5' },
    { key: 'psa10', label: 'PSA 10' },
];
const HORIZONS: { key: string; label: string }[] = [
    { key: '1m', label: '1 month' },
    { key: '6m', label: '6 months' },
    { key: '12m', label: '1 year' },
    { key: 'launch1m', label: 'Early' },
];
const tierLabel = (t: string) => TIERS.find(x => x.key === t)?.label ?? t;
const hzLabel = (h: string) => HORIZONS.find(x => x.key === h)?.label ?? h;
// Fewer graded calls than this and a cell is shown as "still grading".
const MIN_N = 100;
// Weeks thinner than this stay off the trend lines (cohorts land in lumps).
const MIN_WEEK = 1000;

const fmtDate = (d?: string | null) =>
    d ? new Date(d + (d.length === 10 ? 'T12:00:00Z' : '')).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : '';
const fmtWeek = (d: string) =>
    new Date(d + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

// Graded-weighted mean of a field over a set of model rows.
function weighted(rows: ModelScore[], f: (m: ModelScore) => number) {
    const n = rows.reduce((s, r) => s + r.graded, 0);
    return n ? rows.reduce((s, r) => s + r.graded * f(r), 0) / n : 0;
}

// Colour for a band-hit rate: 80% is the calibration target.
function hitColor(hit: number) {
    const t = Math.max(0, Math.min(1, (hit - 0.5) / 0.4));   // 50% → 0, 90% → 1
    const hue = 8 + t * 130;                                  // red → green
    return `hsl(${hue.toFixed(0)} 62% 48%)`;
}

// ----- headline tiles --------------------------------------------------------
function Tile({ value, label, note, tone }: { value: string; label: string; note?: string; tone?: 'up' | 'down' }) {
    return (
        <div className="panel score-tile">
            <div className={`glance__big${tone === 'up' ? ' glance__big--up' : tone === 'down' ? ' glance__big--down' : ''}`}>{value}</div>
            <div className="mono score-tile__label">{label}</div>
            {note && <div className="score-tile__note">{note}</div>}
        </div>
    );
}

function Headline({ models, updatedAt }: { models: ModelScore[]; updatedAt?: string | null }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const nm1m = models.filter(m => m.target === 'ungraded' && m.horizon === '1m');
    const total = models.reduce((s, m) => s + m.graded, 0);
    const hit = weighted(nm1m, m => m.bandHit) * 100;
    const miss = pct(weighted(nm1m, m => m.mae));
    const tracked = models.filter(m => m.graded >= MIN_N).length;
    const vTotal = useCountUp(total, inView);
    const vHit = useCountUp(hit, inView);
    const vMiss = useCountUp(miss, inView);
    const vTracked = useCountUp(tracked, inView);
    return (
        <div ref={ref} className="score-tiles" data-in={inView ? 1 : 0}>
            <Tile value={compact(vTotal)} label="forecasts graded" note="every model, every horizon, since the archive began" />
            <Tile value={`${vHit.toFixed(1)}%`} label="landed inside the 80% range" tone="up"
                note="1-month Near Mint calls · 80% means the ranges are honest" />
            <Tile value={`±${vMiss.toFixed(1)}%`} label="typical miss" note="1-month Near Mint · gap between the call and the landing" />
            <Tile value={`${Math.round(vTracked)}`} label="models with a record" note={`${MIN_N}+ graded calls · through ${fmtDate(updatedAt)}`} />
        </div>
    );
}

// ----- accuracy over time ----------------------------------------------------
type Pt = { week: string; n: number; hit: number; mae: number; flat: number; dir?: number | null };

// Phones get a narrower drawing box so the axis text keeps its size on screen.
function useNarrow() {
    const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches);
    useEffect(() => {
        const mq = window.matchMedia('(max-width: 767px)');
        const on = () => setNarrow(mq.matches);
        mq.addEventListener('change', on);
        return () => mq.removeEventListener('change', on);
    }, []);
    return narrow;
}

function WeeklyChart({ pts }: { pts: Pt[] }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const narrow = useNarrow();
    const W = narrow ? 420 : 1000, H = narrow ? 240 : 230, L = 48, R = 20, T = 18, B = 34;
    const x = (i: number) => L + (pts.length > 1 ? (i / (pts.length - 1)) * (W - L - R) : (W - L - R) / 2);
    // The range chart plots 50%..100% over the full height (nothing lands below).
    const yHit = (v: number) => T + (1 - (v - 0.5) / 0.5) * (H - T - B);
    const maxN = Math.max(...pts.map(p => p.n), 1);
    const missMax = Math.max(...pts.map(p => Math.max(pct(p.mae), pct(p.flat))), 1) * 1.15;
    const yMiss = (v: number) => T + (1 - v / missMax) * (H - T - B);
    const line = (f: (p: Pt) => number) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${f(p).toFixed(1)}`).join(' ');
    if (pts.length < 2) {
        return <div className="glance__text">The weekly record fills in as cohorts land — check back after the next refresh.</div>;
    }
    const maxTicks = narrow ? 4 : 8;
    const ticks = pts.map((p, i) => ({ i, label: fmtWeek(p.week) }))
        .filter((_, i) => pts.length <= maxTicks || i % Math.ceil(pts.length / maxTicks) === 0 || i === pts.length - 1);
    return (
        <div ref={ref} className="score-charts" data-in={inView ? 1 : 0}>
            <figure className="score-chart">
                <figcaption className="mono">Inside the 80% range, by landing week</figcaption>
                <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Share of forecasts that landed inside the model's range, week by week">
                    {[0.5, 0.6, 0.7, 0.8, 0.9, 1].map(v => (
                        <g key={v}>
                            <line className="score-grid" x1={L} x2={W - R} y1={yHit(v)} y2={yHit(v)} />
                            <text className="score-axis" x={L - 6} y={yHit(v) + 3} textAnchor="end">{Math.round(v * 100)}%</text>
                        </g>
                    ))}
                    <line className="score-target" x1={L} x2={W - R} y1={yHit(0.8)} y2={yHit(0.8)} />
                    <text className="score-axis score-axis--target" x={L + 6} y={yHit(0.8) - 5}>80% target</text>
                    <path className="score-line score-line--hit" d={line(p => yHit(Math.max(0.5, p.hit)))} />
                    {pts.map((p, i) => (
                        <circle key={p.week} className="score-dot score-dot--hit" cx={x(i)} cy={yHit(Math.max(0.5, p.hit))}
                            r={3 + 5 * Math.sqrt(p.n / maxN)} style={{ '--i': i } as CSSProperties}>
                            <title>{`Week of ${fmtWeek(p.week)}: ${(p.hit * 100).toFixed(1)}% inside the range · ${p.n.toLocaleString()} graded`}</title>
                        </circle>
                    ))}
                    {ticks.map(t => (
                        <text key={t.i} className="score-axis" x={x(t.i)} y={H - 12} textAnchor="middle">{t.label}</text>
                    ))}
                </svg>
                <div className="score-chart__note">Dot size is how many forecasts landed that week.</div>
            </figure>
            <figure className="score-chart">
                <figcaption className="mono">Typical miss vs. holding flat, by landing week</figcaption>
                <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="The model's typical miss compared with assuming no change, week by week">
                    {[0, 0.25, 0.5, 0.75, 1].map(f => (
                        <g key={f}>
                            <line className="score-grid" x1={L} x2={W - R} y1={yMiss(f * missMax)} y2={yMiss(f * missMax)} />
                            <text className="score-axis" x={L - 6} y={yMiss(f * missMax) + 3} textAnchor="end">±{Math.round(f * missMax)}%</text>
                        </g>
                    ))}
                    <path className="score-line score-line--flat" d={line(p => yMiss(pct(p.flat)))} />
                    <path className="score-line score-line--miss" d={line(p => yMiss(pct(p.mae)))} />
                    {pts.map((p, i) => (
                        <circle key={p.week} className="score-dot score-dot--miss" cx={x(i)} cy={yMiss(pct(p.mae))} r={3.5}
                            style={{ '--i': i } as CSSProperties}>
                            <title>{`Week of ${fmtWeek(p.week)}: model ±${pct(p.mae).toFixed(1)}% · holding flat ±${pct(p.flat).toFixed(1)}%`}</title>
                        </circle>
                    ))}
                    {ticks.map(t => (
                        <text key={t.i} className="score-axis" x={x(t.i)} y={H - 12} textAnchor="middle">{t.label}</text>
                    ))}
                </svg>
                <div className="score-chart__note">
                    <span><span className="glance__dot glance__dot--brand" />model</span>
                    <span><span className="glance__dot glance__dot--bench" />holding flat (assume no change)</span>
                </div>
            </figure>
        </div>
    );
}

function OverTime({ weekly }: { weekly: WeekScore[] }) {
    const [slice, setSlice] = useState<'all' | '10'>('all');
    const pts: Pt[] = useMemo(() => weekly
        .map(w => slice === 'all'
            ? { week: w.week, n: w.n, hit: w.bandHit ?? 0, mae: w.mae ?? 0, flat: w.flatMae ?? 0, dir: w.dirHit }
            : { week: w.week, n: w.n10 ?? 0, hit: w.bandHit10 ?? 0, mae: w.mae10 ?? 0, flat: w.flatMae10 ?? 0, dir: w.dirHit })
        .filter(p => p.n >= MIN_WEEK), [weekly, slice]);
    const latest = pts[pts.length - 1];
    const beatsFlat = pts.filter(p => p.mae < p.flat).length;
    const gap = pts.length ? pts.reduce((s, p) => s + Math.abs(pct(p.mae) - pct(p.flat)), 0) / pts.length : 0;
    return (
        <section className="panel score-section">
            <div className="band">
                <span className="band__title">Accuracy over time</span>
                <span className="mono">1-month Near Mint cohorts · by the week they came due</span>
            </div>
            <div className="score-section__intro">
                <p className="glance__text">
                    Every night the model issues a 1-month call on every priced Near Mint card. Twenty-eight days
                    later each call is graded against the real price. Grouping the graded calls by the week they
                    landed shows whether the ranges are staying honest and whether the point calls beat simply
                    assuming nothing changes.
                    {latest && ` Latest full week: ${(latest.hit * 100).toFixed(0)}% inside the range on ${latest.n.toLocaleString()} calls.`}
                </p>
                <div className="score-tabs" role="tablist" aria-label="Which cards">
                    <button type="button" role="tab" aria-selected={slice === 'all'} className="score-tab" onClick={() => setSlice('all')}>All cards</button>
                    <button type="button" role="tab" aria-selected={slice === '10'} className="score-tab" onClick={() => setSlice('10')}>Cards $10+</button>
                </div>
            </div>
            <WeeklyChart pts={pts} />
            {pts.length > 1 && (
                <div className="glance__foot">
                    Across the weeks shown the model's typical miss ran {gap.toFixed(1)} points from holding flat and
                    beat it in {beatsFlat} of {pts.length}. Point calls are damped toward today's price wherever the
                    model's edge is weak, so the two track closely; the ranges, graded above, are where the model
                    earns its keep. Weeks with fewer than {MIN_WEEK.toLocaleString()} landed calls are left off the lines.
                </div>
            )}
        </section>
    );
}

// ----- every model: the heatmap ----------------------------------------------
function Heatmap({ models }: { models: ModelScore[] }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const [tier, setTier] = useState('ungraded');
    const tiers = TIERS.filter(t => models.some(m => m.target === t.key));
    const rows = models.filter(m => m.target === tier);
    const games = [...new Map(rows.map(m => [m.game, m.label])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    const cols = HORIZONS.filter(h => rows.some(m => m.horizon === h.key));
    const cell = (game: string, hz: string) => rows.find(m => m.game === game && m.horizon === hz);
    return (
        <section ref={ref} className="panel score-section" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">Every model</span>
                <span className="mono">share of outcomes inside the 80% range</span>
            </div>
            <div className="score-section__intro">
                <p className="glance__text">
                    One model per game, condition tier and horizon. A cell shows how often reality landed inside that
                    model's low-to-high range; 80% is the target, higher is over-cautious, lower is over-confident.
                    Below each figure is the graded count. The longer horizons are young: a 1-year call issued when
                    the archive began is only now coming due.
                </p>
                <div className="score-tabs" role="tablist" aria-label="Condition tier">
                    {tiers.map(t => (
                        <button type="button" role="tab" key={t.key} aria-selected={tier === t.key} className="score-tab"
                            onClick={() => setTier(t.key)}>{t.label}</button>
                    ))}
                </div>
            </div>
            <div className="score-heat" style={{ '--cols': cols.length } as CSSProperties}>
                <div className="score-heat__corner" />
                {cols.map(c => <div key={c.key} className="mono score-heat__col">{c.label}</div>)}
                {games.map(([game, label], gi) => (
                    <div className="score-heat__row" key={game} style={{ '--i': gi } as CSSProperties}>
                        <div className="score-heat__game"><span className="glance__dot" style={{ background: gameColor(game) }} />{label}</div>
                        {cols.map(c => {
                            const m = cell(game, c.key);
                            if (!m) return <div key={c.key} className="score-heat__cell score-heat__cell--none" title="No forecasts issued yet">·</div>;
                            const thin = m.graded < MIN_N;
                            return (
                                <div key={c.key} className={`score-heat__cell${thin ? ' score-heat__cell--thin' : ''}`}
                                    style={thin ? undefined : { '--c': hitColor(m.bandHit) } as CSSProperties}
                                    title={`${label} · ${tierLabel(m.target)} · ${hzLabel(m.horizon)}: ${(m.bandHit * 100).toFixed(1)}% inside the range, typical miss ±${pct(m.mae).toFixed(1)}%, runs ${pct(m.bias).toFixed(1)}% ${m.bias > 0 ? 'hot' : 'cold'}, ${m.graded.toLocaleString()} graded`}>
                                    <span className="score-heat__val">{thin ? 'still grading' : `${Math.round(m.bandHit * 100)}%`}</span>
                                    <span className="mono score-heat__n">{m.graded.toLocaleString()} graded</span>
                                </div>
                            );
                        })}
                    </div>
                ))}
            </div>
            <div className="score-heat__legend mono">
                <span>over-confident</span>
                <span className="score-heat__scale" />
                <span>calibrated (80%)</span>
                <span className="score-heat__scale score-heat__scale--hi" />
                <span>over-cautious</span>
            </div>
        </section>
    );
}

// ----- by condition tier -----------------------------------------------------
function ByTier({ models }: { models: ModelScore[] }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const rows = TIERS.map(t => {
        const r = models.filter(m => m.target === t.key && m.horizon === '1m');
        return { ...t, graded: r.reduce((s, m) => s + m.graded, 0), hit: weighted(r, m => m.bandHit), miss: pct(weighted(r, m => m.mae)) };
    }).filter(r => r.graded >= MIN_N);
    const missMax = Math.max(...rows.map(r => r.miss), 1);
    return (
        <section ref={ref} className="panel score-section score-section--half" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">By condition</span>
                <span className="mono">1 month · all games</span>
            </div>
            <p className="glance__text">
                Raw cards are priced from daily TCGplayer sales; graded tiers from PriceCharting's per-grade
                history. The tiers grade about equally well, which says the ranges are set by the method, not the
                price source.
            </p>
            <div className="score-tier">
                <div className="mono score-tier__head"><span>tier</span><span>inside range</span><span>typical miss</span><span>graded</span></div>
                {rows.map((r, i) => (
                    <div className="score-tier__row" key={r.key} style={{ '--i': i } as CSSProperties}>
                        <span className="score-tier__label">{r.label}</span>
                        <span className="glance__track"><span className="glance__fill" style={{ '--w': r.hit, '--c': hitColor(r.hit) } as CSSProperties} /><span className="score-tier__target" /></span>
                        <span className="score-tier__val mono">{(r.hit * 100).toFixed(1)}%</span>
                        <span className="glance__track"><span className="glance__fill" style={{ '--w': r.miss / missMax, '--c': '#5b6782' } as CSSProperties} /></span>
                        <span className="score-tier__val mono">±{r.miss.toFixed(1)}%</span>
                        <span className="score-tier__n mono">{compact(r.graded)}</span>
                    </div>
                ))}
            </div>
            <div className="glance__foot">The tick on each range bar marks the 80% target.</div>
        </section>
    );
}

// ----- which way it misses ---------------------------------------------------
function Bias({ models }: { models: ModelScore[] }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const rows = models.filter(m => m.target === 'ungraded' && m.horizon === '1m' && m.graded >= MIN_N)
        .map(m => ({ ...m, biasPct: (Math.exp(m.bias) - 1) * 100 }))
        .sort((a, b) => b.biasPct - a.biasPct);
    const lim = Math.max(...rows.map(r => Math.abs(r.biasPct)), 1) * 1.1;
    return (
        <section ref={ref} className="panel score-section score-section--half" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">Which way it misses</span>
                <span className="mono">1 month · Near Mint · lean of the error</span>
            </div>
            <p className="glance__text">
                The average signed gap between the call and the landing. Right of centre the model has been
                running hot (calls above where prices went); left of centre, cold. A calibrated model sits near zero
                and the nightly recalibration pulls each game back toward it.
            </p>
            <div className="score-bias">
                {rows.map((r, i) => (
                    <div className="score-bias__row" key={r.game} style={{ '--i': i } as CSSProperties}
                        title={`${r.label}: runs ${Math.abs(r.biasPct).toFixed(1)}% ${r.biasPct > 0 ? 'hot' : 'cold'} over ${r.graded.toLocaleString()} graded`}>
                        <span className="score-bias__label">{r.label}</span>
                        <span className="score-bias__track">
                            <span className="score-bias__zero" />
                            <span className={`score-bias__bar${r.biasPct >= 0 ? ' score-bias__bar--hot' : ' score-bias__bar--cold'}`}
                                style={{ '--w': Math.abs(r.biasPct) / lim / 2, '--c': gameColor(r.game) } as CSSProperties} />
                        </span>
                        <span className="mono score-bias__val">{r.biasPct >= 0 ? '+' : '−'}{Math.abs(r.biasPct).toFixed(1)}% {r.biasPct >= 0 ? 'hot' : 'cold'}</span>
                    </div>
                ))}
            </div>
            <div className="score-bias__axis mono">
                <span />
                <span className="score-bias__axis-track"><span>◂ cold</span><span>0</span><span>hot ▸</span></span>
                <span />
            </div>
        </section>
    );
}

// ----- the early tier --------------------------------------------------------
function Early({ models }: { models: ModelScore[] }) {
    const { ref, inView } = useInView<HTMLDivElement>();
    const rows = models.filter(m => m.horizon === 'launch1m' && m.target === 'ungraded');
    const graded = rows.reduce((s, m) => s + m.graded, 0);
    const hit = weighted(rows, m => m.bandHit) * 100;
    const miss = pct(weighted(rows, m => m.mae));
    const vGraded = useCountUp(graded, inView);
    if (!graded) return null;
    return (
        <section ref={ref} className="panel score-section" data-in={inView ? 1 : 0}>
            <div className="band">
                <span className="band__title">The early tier</span>
                <span className="mono">brand-new cards · first-month call</span>
            </div>
            <div className="score-early">
                <div className="score-early__stats">
                    <div className="glance__stat"><div className="glance__big">{compact(vGraded)}</div><div className="mono">early calls graded</div></div>
                    <div className="glance__stat"><div className={`glance__big${hit >= 70 ? ' glance__big--up' : ' glance__big--down'}`}>{hit.toFixed(0)}%</div><div className="mono">inside the range</div></div>
                    <div className="glance__stat"><div className="glance__big">±{miss.toFixed(0)}%</div><div className="mono">typical miss</div></div>
                </div>
                <p className="glance__text">
                    A card with under two months of prices gets an <strong>EARLY</strong> call instead of a regular
                    forecast: the model's first-month estimate, labelled low confidence and kept out of the movers.
                    It is held to the same grading, and the record is what you would expect of a call made on a
                    week of sales: wide misses, useful for ranking a new set's chase cards, not for pricing them.
                </p>
                <ul className="glance__bars">
                    {rows.filter(m => m.graded >= 20).sort((a, b) => b.bandHit - a.bandHit).map((m, i) => (
                        <li key={m.game} className="glance__barrow" style={{ '--i': i } as CSSProperties}
                            title={`${m.label}: ${(m.bandHit * 100).toFixed(0)}% inside the range over ${m.graded.toLocaleString()} early calls`}>
                            <span className="glance__barlabel">{m.label}</span>
                            <span className="glance__track"><span className="glance__fill" style={{ '--w': m.bandHit, '--c': gameColor(m.game) } as CSSProperties} /></span>
                            <span className="mono glance__barval">{Math.round(m.bandHit * 100)}%</span>
                        </li>
                    ))}
                </ul>
            </div>
        </section>
    );
}

// ----- page ------------------------------------------------------------------
export default function AboutPage() {
    usePageMeta("Model performance", "How CardStock's price forecasts have actually done: every model's graded record, week by week.");
    const { data, isError } = useFetchScorecardQuery();
    const models = data?.models ?? [];
    return (
        <article className="article article--wide full-span score">
            <header className="article__head">
                <span className="kicker">Model performance</span>
                <h1 className="article__title display">How the forecasts have actually done</h1>
                <div className="mono article__meta">
                    {data?.updatedAt ? `Graded through ${fmtDate(data.updatedAt)} · ` : ''}updated with every refresh · model forecast-deep-v4.5
                </div>
                <p className="article__lede">
                    Every forecast CardStock publishes is saved the day it is issued and graded against the real
                    price once its date arrives. Nothing is re-scored or quietly forgotten: the numbers count every
                    forecast ever graded. What follows is that whole record for every model, the misses included.
                </p>
            </header>

            {isError && <div className="panel score-section glance__text">The scorecard is not available right now. Try again in a moment.</div>}
            {!data && !isError && <div className="panel score-section glance__text">Loading the record…</div>}

            {data && (
                <>
                    <Headline models={models} updatedAt={data.updatedAt} />
                    <OverTime weekly={data.weekly} />
                    <Heatmap models={models} />
                    <div className="score-pair">
                        <ByTier models={models} />
                        <Bias models={models} />
                    </div>
                    <Early models={models} />
                </>
            )}

            <section className="panel score-section score-method">
                <div className="band">
                    <span className="band__title">How the grading works</span>
                    <span className="mono">and what the model is</span>
                </div>
                <div className="score-method__cols">
                    <div>
                        <h3>The call</h3>
                        <p>
                            For each game, condition tier and horizon a gradient-boosted model predicts a card's future
                            return from its price history, its set, its game, the hobby as a whole, its rarity, printed
                            stats and artwork, and its own past misses. Two companion models predict the optimistic and
                            pessimistic cases to form an 80% range, widened on past errors until it covers 80% of outcomes.
                        </p>
                    </div>
                    <div>
                        <h3>The grade</h3>
                        <p>
                            A 1-month call comes due 28 days after it is issued; longer horizons at their month. The
                            landing price is the card's real market price on that date. <em>Inside the range</em> means
                            the landing fell between the call's low and high. <em>Typical miss</em> is the average gap
                            between the point call and the landing, and <em>holding flat</em> is the same gap for assuming
                            today's price.
                        </p>
                    </div>
                    <div>
                        <h3>The feedback</h3>
                        <p>
                            Graded outcomes feed the next retrain, and each game's point calls are scaled to what its
                            recently graded calls support. The Friday <Link to="/reports">market report</Link> publishes
                            the same scorecard. None of this is financial advice: a forecast cannot know about a reprint,
                            a ban or next month's tournament results.
                        </p>
                    </div>
                </div>
            </section>
        </article>
    );
}
