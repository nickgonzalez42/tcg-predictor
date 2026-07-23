import { useEffect, useRef, useState } from "react";
import { createChart, AreaSeries, LineSeries, ColorType, LineStyle } from "lightweight-charts";
import type { ISeriesApi, SeriesType, Time } from "lightweight-charts";
import { useFetchCardHistoryQuery, useFetchCardForecastHistoryQuery } from "./catalogApi";
import type { Forecast, PastForecast } from "../../app/models/card";
import { GRADE_TIERS, GRADE_TIER_LABEL } from "../watchlist/grades";

const RANGES: { key: string; label: string; months?: number }[] = [
    { key: '1m', label: '1M', months: 1 },
    { key: '6m', label: '6M', months: 6 },
    { key: '1y', label: '1Y', months: 12 },
    { key: 'all', label: 'ALL' },
];

type Props = {
    game: string;
    id: number;
    forecasts?: Forecast[];   // model forecasts; the tier matching the shown grade is drawn dashed
};

function addMonths(date: string, months: number) {
    const d = new Date(date + 'T00:00:00Z');
    d.setUTCMonth(d.getUTCMonth() + months);
    return d.toISOString().slice(0, 10);
}

function addDays(date: string, days: number) {
    const d = new Date(date + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
}

// Where each forecast horizon lands on the time axis, from the last real point.
// Fixed week-based lengths (4/26/52 weeks): month arithmetic has no answer for
// "Aug 31 + 1 month" (setUTCMonth would roll it into October). The site serves
// 1m/6m/12m only (1w stays pipeline-internal until the true weekly model ships).
const HORIZON_OFFSET: Record<string, (date: string) => string> = {
    '1m': d => addDays(d, 28),
    '6m': d => addDays(d, 182),
    '12m': d => addDays(d, 364),
};

// Past forecasts for the shown tier, as dots placed at the date each forecast
// was aiming at (a 1M generated June 20 plots 28 days later on July 18, right
// against what the price actually did). The API only returns matured ones
// (target date already passed, so a 1Y only appears once it is over a year
// old). The view picker
// chooses what to plot: "latest" = the most recently matured one per horizon
// (e.g. the 1M issued a month ago); a horizon key = every matured forecast of
// that one category.
const PAST_HORIZONS = ['1m', '6m', '12m'];
const HORIZON_LABEL: Record<string, string> = { '1m': '1M', '6m': '6M', '12m': '1Y' };

// At most one dot per 28-day slot, slots anchored at today (today−28d,
// today−56d, …; "a month" is always exactly 28 days — calendar months vary and
// can name impossible dates). Each slot takes the not-yet-used forecast whose
// issue date is closest to it (within 14 days, so the slots tile the timeline
// with no gaps); when two forecasts were issued days apart, the one nearest a
// whole 28-day step from today wins and the rest stay hidden.
function monthlyIncrements(candidates: PastForecast[]): PastForecast[] {
    const dated = candidates.filter(f => f.asOf);
    if (dated.length <= 1) return dated;
    const STEP = 28 * 86400e3;
    const HALF_STEP = 14 * 86400e3;
    const oldest = Math.min(...dated.map(f => Date.parse(f.asOf!)));
    const today = Date.now();
    const used = new Set<PastForecast>();
    const picks: PastForecast[] = [];
    for (let k = 1; ; k++) {
        const slot = today - k * STEP;
        if (slot < oldest - HALF_STEP) break;
        let best: PastForecast | undefined;
        let bestDist = HALF_STEP;
        for (const f of dated) {
            if (used.has(f)) continue;
            const dist = Math.abs(Date.parse(f.asOf!) - slot);
            if (dist <= bestDist) { best = f; bestDist = dist; }
        }
        if (best) { used.add(best); picks.push(best); }
    }
    return picks.sort((a, b) => (a.asOf! < b.asOf! ? -1 : 1));
}

function pickPastForecasts(past: PastForecast[], grade: string, view: string): PastForecast[] {
    if (view !== 'latest')
        return monthlyIncrements(past.filter(f => f.target === grade && f.horizon === view));
    return PAST_HORIZONS.flatMap(horizon => {
        const candidates = past.filter(f => f.target === grade && f.horizon === horizon);
        return candidates.length
            ? [candidates.reduce((a, b) => (a.targetDate > b.targetDate ? a : b))]
            : [];
    });
}

export default function PriceHistoryChart({ game, id, forecasts }: Props) {
    const { data, isLoading } = useFetchCardHistoryQuery({ game, id });
    const { data: pastData } = useFetchCardForecastHistoryQuery({ game, id });
    const containerRef = useRef<HTMLDivElement>(null);
    const [grade, setGrade] = useState('ungraded');
    const [range, setRange] = useState('all');
    const [hidden, setHidden] = useState<Set<string>>(new Set());
    // 'latest' = most recent matured per horizon; a horizon key = all of that category.
    const [pastView, setPastView] = useState('latest');

    const toggleKey = (id_: string) => setHidden(prev => {
        const next = new Set(prev);
        if (next.has(id_)) next.delete(id_); else next.add(id_);
        return next;
    });

    // Live handles to the drawn series, so hovering a legend key can thicken
    // its line in place (applyOptions) without rebuilding the chart.
    const seriesByKey = useRef<Record<string, ISeriesApi<SeriesType>[]>>({});

    const highlightKey = (key: string | null) => {
        for (const [k, list] of Object.entries(seriesByKey.current)) {
            const hot = k === key;
            for (const s of list) {
                if (k === 'history' || k === 'forecast')
                    s.applyOptions({ lineWidth: hot ? 4 : 2 });
                else
                    s.applyOptions({ pointMarkersRadius: hot ? 5 : 3 });   // past-forecast dots
            }
        }
    };

    const grades = data ? GRADE_TIERS.filter(g => data.series[g]?.length) : [];

    // default to the first available tier once data arrives
    useEffect(() => {
        if (grades.length && !grades.includes(grade)) setGrade(grades[0]);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data]);

    // A category view resets when switching tiers (that horizon may not exist there).
    useEffect(() => { setPastView('latest'); }, [grade]);

    useEffect(() => {
        const el = containerRef.current;
        const all = data?.series[grade];
        if (!el || !all?.length) return;

        // Range filter (monthly points, measured back from the latest one).
        const months = RANGES.find(r => r.key === range)?.months;
        const cutoff = months ? addMonths(all[all.length - 1].date, -months) : null;
        const points = cutoff ? all.filter(p => p.date >= cutoff) : all;
        if (!points.length) return;

        // Theme colors come from the CSS variables so both palettes stay in sync.
        const css = getComputedStyle(el);
        const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
        const history = v('--chart-history', '#3d7dca');
        const forecastColor = v('--chart-forecast', '#e0b000');
        const textMuted = v('--text-muted', '#8b96ad');
        const border = v('--border', '#2e3a52');

        const chart = createChart(el, {
            height: 340,
            autoSize: true,
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: textMuted },
            grid: { vertLines: { color: border }, horzLines: { color: border } },
            rightPriceScale: { borderColor: border },
            timeScale: { borderColor: border },
            handleScroll: false,   // no pan / mouse-wheel scroll (page scrolls normally over it)
            handleScale: false,    // no zoom; chart stays fit to the full range
        });
        seriesByKey.current = {};
        const track = (key: string, s: ISeriesApi<SeriesType>) =>
            (seriesByKey.current[key] ??= []).push(s);

        if (!hidden.has('history')) {
            const series = chart.addSeries(AreaSeries, {
                lineColor: history,
                topColor: 'rgba(61, 125, 202, 0.30)',
                bottomColor: 'rgba(61, 125, 202, 0.02)',
                lineWidth: 2,
                priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
            });
            series.setData(points.map(p => ({ time: p.date, value: p.price })));
            track('history', series);
        }

        // Dashed gold forecast chain: one SEGMENT per period, each continuing
        // from the previous horizon's endpoint — last real point -> 1w -> 1m
        // -> 6m -> 12m — with a dot marking each horizon along the way.
        const tierFc = hidden.has('forecast') ? [] : (forecasts ?? [])
            .filter(f => f.target === grade && HORIZON_OFFSET[f.horizon]);
        const last = points[points.length - 1];
        const chainPts = [
            { time: last.date, value: last.price },
            ...tierFc
                .map(f => ({ time: HORIZON_OFFSET[f.horizon](last.date), value: f.forecastPrice }))
                .sort((a, b) => a.time.localeCompare(b.time)),
        ];
        for (let i = 0; i + 1 < chainPts.length; i++) {
            const seg = chart.addSeries(LineSeries, {
                color: forecastColor,
                lineWidth: 2,
                lineStyle: LineStyle.Dashed,
                pointMarkersVisible: true,
                pointMarkersRadius: 3,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            seg.setData([chainPts[i], chainPts[i + 1]]);
            track('forecast', seg);
        }

        // Past-forecast review: each matured prediction is a single dot placed
        // at (the date it predicted FOR, the price it predicted) — the vertical
        // gap to the history line at that date IS the miss. Coloured by horizon,
        // line hidden — the point is the whole mark. Its details (horizon,
        // generation date, price) show on hover / tap via the tooltip.
        const pastColors: Record<string, string> = {
            '1m': v('--chart-past-1m', '#c678dd'),
            '6m': v('--chart-past-6m', '#ff9e64'),
            '12m': v('--chart-past-12m', '#f06292'),
        };
        type PointMeta = {
            series: ISeriesApi<SeriesType>; horizon: string; asOf: string;
            targetDate: string; price: number; base?: number;
        };
        const pastPointMeta: PointMeta[] = [];
        const pastPicks = pickPastForecasts(pastData?.forecasts ?? [], grade, pastView)
            .filter(p => !hidden.has(p.horizon))
            // Keep the dot inside the visible window so old ones don't stretch the axis.
            .filter(p => p.asOf && (!cutoff || p.targetDate >= cutoff));
        for (const p of pastPicks) {
            // Permanently inkless anchor holding this forecast's generation
            // date (and base price) on the time axis. The hover trajectory is
            // drawn by ONE shared overlay series below — but if these times
            // only appeared when hovered, the index-based axis would re-space
            // mid-hover and slide the dots out from under the cursor.
            if (p.basePrice != null) {
                const anchor = chart.addSeries(LineSeries, {
                    lineVisible: false,
                    pointMarkersVisible: false,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                anchor.setData([{ time: p.asOf!, value: p.basePrice },
                                { time: p.targetDate, value: p.forecastPrice }]);
            }
            const dot = chart.addSeries(LineSeries, {
                color: pastColors[p.horizon] ?? '#c678dd',
                lineVisible: false,          // markers only — no connecting line
                pointMarkersVisible: true,
                pointMarkersRadius: 3,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            dot.setData([{ time: p.targetDate, value: p.forecastPrice }]);
            track(p.horizon, dot);
            pastPointMeta.push({ series: dot, horizon: p.horizon, asOf: p.asOf!,
                                 targetDate: p.targetDate, price: p.forecastPrice,
                                 base: p.basePrice });
        }

        // One shared overlay draws the hovered dot's trajectory (generation
        // point -> predicted point) by swapping its data; empty data = hidden.
        // Its times always exist via the anchors, so the axis never moves.
        const traj = chart.addSeries(LineSeries, {
            color: '#c678dd',
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            pointMarkersVisible: true,
            pointMarkersRadius: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        let shownTraj: PointMeta | null = null;
        const showLink = (m: PointMeta | null) => {
            if (m === shownTraj) return;
            shownTraj = m;
            if (!m || m.base == null) { traj.setData([]); return; }
            traj.applyOptions({ color: pastColors[m.horizon] ?? '#c678dd' });
            traj.setData([{ time: m.asOf, value: m.base },
                          { time: m.targetDate, value: m.price }]);
        };

        // Hover (desktop) / tap (touch) tooltip for the past-forecast dots.
        // lightweight-charts fires crosshair moves for taps too, so one handler
        // covers both. seriesData only carries a dot's series at its own time.
        el.style.position = 'relative';
        const tip = document.createElement('div');
        tip.className = 'chart-tip';
        tip.style.display = 'none';
        el.appendChild(tip);
        const fmtDate = (d: string) =>
            new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });

        const renderTip = (m: PointMeta, x: number, y: number) => {
            tip.innerHTML =
                `<strong>${HORIZON_LABEL[m.horizon] ?? m.horizon} forecast</strong>` +
                `<span>generated ${fmtDate(m.asOf)}</span>` +
                `<span>$${m.price.toFixed(2)}</span>`;
            tip.style.display = 'flex';   // matches .chart-tip's column layout — 'block' would collapse the lines
            const left = Math.min(Math.max(x + 12, 4), el.clientWidth - tip.offsetWidth - 4);
            tip.style.left = `${left}px`;
            tip.style.top = `${Math.max(y - tip.offsetHeight - 10, 4)}px`;
        };

        chart.subscribeCrosshairMove(param => {
            if (!param.point || !pastPointMeta.length) { tip.style.display = 'none'; showLink(null); return; }
            // Pixel hit-test against every dot's own screen position — the
            // crosshair's seriesData only reports series with data at the
            // hovered slot, which silently skipped dots on axis slots no other
            // series shares. Coordinates treat all dots alike.
            let best: PointMeta | null = null;
            let bestD = Infinity;
            for (const m of pastPointMeta) {
                const x = chart.timeScale().timeToCoordinate(m.targetDate as Time);
                const y = m.series.priceToCoordinate(m.price);
                if (x == null || y == null) continue;
                const d = Math.hypot(x - param.point.x, y - param.point.y);
                if (d < bestD) { bestD = d; best = m; }
            }
            if (!best || bestD > 20) { tip.style.display = 'none'; showLink(null); return; }
            const y = best.series.priceToCoordinate(best.price) ?? param.point.y;
            renderTip(best, param.point.x, y);
            showLink(best);
        });

        chart.timeScale().fitContent();

        return () => {
            seriesByKey.current = {};
            tip.remove();
            chart.remove();
        };
    }, [data, grade, range, forecasts, pastData, hidden, pastView]);

    if (isLoading) return <div>Loading chart…</div>;
    if (!grades.length) return <div className="est-note">No price history yet for this card.</div>;

    const hasForecast = (forecasts ?? []).some(f => f.target === grade);
    const pastPicks = pickPastForecasts(pastData?.forecasts ?? [], grade, pastView);
    const hasPast = (pastData?.forecasts ?? []).some(f => f.target === grade);

    // Clickable legend: one key per drawn series; clicking toggles that line.
    // Split into rows: history/forecast, then the past-forecast horizons.
    const mainKeys = [
        { id: 'history', label: 'History', color: 'var(--chart-history)' },
        ...(hasForecast ? [{ id: 'forecast', label: 'Forecast', color: 'var(--chart-forecast)' }] : []),
    ];
    // One key per horizon that has any plotted dot (not one per dot).
    const pastKeys = PAST_HORIZONS.filter(h => pastPicks.some(p => p.horizon === h)).map(h => ({
        id: h,
        label: HORIZON_LABEL[h] ?? h,
        color: `var(--chart-past-${h})`,
    }));

    const renderKey = (k: { id: string; label: string; color: string }) => (
        <button
            key={k.id}
            className={`chart-legend__key${hidden.has(k.id) ? ' chart-legend__key--off' : ''}`}
            onClick={() => toggleKey(k.id)}
            onMouseEnter={() => highlightKey(k.id)}
            onMouseLeave={() => highlightKey(null)}
            title={hidden.has(k.id) ? 'Show this line' : 'Hide this line'}
        >
            <span className="chart-legend__swatch" style={{ background: k.color }} />
            {k.label}
        </button>
    );

    return (
        <div>
            <div className="chart-tabs">
                <div className="grade-tabs">
                    {grades.map(g => (
                        <button
                            key={g}
                            className={`btn btn--outline${g === grade ? ' btn--active' : ''}`}
                            onClick={() => setGrade(g)}
                        >
                            {GRADE_TIER_LABEL[g] ?? g}
                        </button>
                    ))}
                </div>
                <div className="range-tabs" role="group" aria-label="Time range">
                    {RANGES.map(r => (
                        <button
                            key={r.key}
                            className={`btn btn--outline range-tab${r.key === range ? ' btn--active' : ''}`}
                            onClick={() => setRange(r.key)}
                        >
                            {r.label}
                        </button>
                    ))}
                </div>
            </div>
            <div ref={containerRef} style={{ width: '100%' }} />
            <div className="chart-legend mono">
                {mainKeys.map(renderKey)}
            </div>
            {pastKeys.length > 0 && (
                <div className="chart-legend mono">
                    <span className="chart-legend__label">Past forecasts:</span>
                    {pastKeys.map(renderKey)}
                </div>
            )}
            {hasPast && (
                <div className="chart-legend mono">
                    <select className="chart-pastview" value={pastView}
                        aria-label="Past forecasts shown"
                        title="Which past forecasts to plot. Only ones whose target date has passed are shown."
                        onChange={e => setPastView(e.target.value)}>
                        <option value="latest">Latest per timeframe</option>
                        {PAST_HORIZONS
                            .filter(h => (pastData?.forecasts ?? []).some(f => f.target === grade && f.horizon === h))
                            .map(h => (
                                <option key={h} value={h}>All {HORIZON_LABEL[h] ?? h}</option>
                            ))}
                    </select>
                </div>
            )}
        </div>
    );
}
