import { useEffect, useRef, useState } from "react";
import { createChart, AreaSeries, LineSeries, ColorType, LineStyle } from "lightweight-charts";
import type { ISeriesApi, SeriesType, Time } from "lightweight-charts";
import { useFetchCardHistoryQuery, useFetchCardForecastHistoryQuery } from "./catalogApi";
import type { Forecast, PastForecast } from "../../app/models/card";

const GRADE_ORDER = ['ungraded', 'grade7', 'grade8', 'grade9', 'grade95', 'psa10', 'bgs10', 'cgc10', 'sgc10'];
const GRADE_LABEL: Record<string, string> = {
    ungraded: 'Ungraded',
    grade7: 'Grade 7', grade8: 'Grade 8', grade9: 'Grade 9',
    grade95: 'Grade 9.5', psa10: 'PSA 10', bgs10: 'BGS 10', cgc10: 'CGC 10', sgc10: 'SGC 10',
};

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


// Where each forecast horizon lands on the time axis, from the last real point.
// The site serves 1m/6m/12m only (1w stays pipeline-internal until the true
// weekly model ships).
const HORIZON_OFFSET: Record<string, (date: string) => string> = {
    '1m': d => addMonths(d, 1),
    '6m': d => addMonths(d, 6),
    '12m': d => addMonths(d, 12),
};

// Past forecasts for the shown tier, as dots placed at the month each was
// generated from. The API only returns matured ones (target date already
// passed, so a 1Y only appears once it is over a year old). The view picker
// chooses what to plot: "latest" = the most recently matured one per horizon
// (e.g. the 1M issued a month ago); a horizon key = every matured forecast of
// that one category.
const PAST_HORIZONS = ['1m', '6m', '12m'];
const HORIZON_LABEL: Record<string, string> = { '1m': '1M', '6m': '6M', '12m': '1Y' };

function pickPastForecasts(past: PastForecast[], grade: string, view: string): PastForecast[] {
    if (view !== 'latest')
        return past.filter(f => f.target === grade && f.horizon === view);
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

    // Set inside the chart effect: shows the past-dot tooltip for a hovered
    // legend key (same info as hovering the dot itself); null hides it.
    const legendTip = useRef<((horizon: string | null) => void) | null>(null);

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

    const grades = data ? GRADE_ORDER.filter(g => data.series[g]?.length) : [];

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
        // at (when it was generated, the price it predicted), coloured by
        // horizon. Line hidden — the point is the whole mark. Its details
        // (horizon, generation date, price) show on hover / tap via the tooltip.
        const pastColors: Record<string, string> = {
            '1m': v('--chart-past-1m', '#c678dd'),
            '6m': v('--chart-past-6m', '#ff9e64'),
            '12m': v('--chart-past-12m', '#f06292'),
        };
        type PointMeta = { series: ISeriesApi<SeriesType>; horizon: string; asOf: string; price: number };
        const pastPointMeta: PointMeta[] = [];
        const pastPicks = pickPastForecasts(pastData?.forecasts ?? [], grade, pastView)
            .filter(p => !hidden.has(p.horizon))
            // Keep the dot inside the visible window so old ones don't stretch the axis.
            .filter(p => p.asOf && (!cutoff || p.asOf >= cutoff));
        for (const p of pastPicks) {
            const dot = chart.addSeries(LineSeries, {
                color: pastColors[p.horizon] ?? '#c678dd',
                lineVisible: false,          // markers only — no connecting line
                pointMarkersVisible: true,
                pointMarkersRadius: 3,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            dot.setData([{ time: p.asOf!, value: p.forecastPrice }]);
            track(p.horizon, dot);
            pastPointMeta.push({ series: dot, horizon: p.horizon, asOf: p.asOf!, price: p.forecastPrice });
        }

        // Hover (desktop) / tap (touch) tooltip for the past-forecast dots.
        // lightweight-charts fires crosshair moves for taps too, so one handler
        // covers both. seriesData only carries a dot's series at its own time.
        el.style.position = 'relative';
        const tip = document.createElement('div');
        tip.className = 'chart-tip';
        tip.style.display = 'none';
        el.appendChild(tip);
        const fmtMonth = (d: string) =>
            new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });

        const renderTip = (m: PointMeta, x: number, y: number) => {
            tip.innerHTML =
                `<strong>${HORIZON_LABEL[m.horizon] ?? m.horizon} forecast</strong>` +
                `<span>generated ${fmtMonth(m.asOf)}</span>` +
                `<span>$${m.price.toFixed(2)}</span>`;
            tip.style.display = 'block';
            const left = Math.min(Math.max(x + 12, 4), el.clientWidth - tip.offsetWidth - 4);
            tip.style.left = `${left}px`;
            tip.style.top = `${Math.max(y - tip.offsetHeight - 10, 4)}px`;
        };

        chart.subscribeCrosshairMove(param => {
            if (!param.point || param.time == null || !pastPointMeta.length) { tip.style.display = 'none'; return; }
            // Among dots present at the hovered time, take the one nearest the cursor.
            let best: PointMeta | null = null;
            let bestDy = Infinity;
            for (const m of pastPointMeta) {
                if (!param.seriesData.has(m.series)) continue;
                const y = m.series.priceToCoordinate(m.price);
                if (y == null) continue;
                const dy = Math.abs(y - param.point.y);
                if (dy < bestDy) { bestDy = dy; best = m; }
            }
            if (!best || bestDy > 28) { tip.style.display = 'none'; return; }
            const y = best.series.priceToCoordinate(best.price) ?? param.point.y;
            renderTip(best, param.point.x, y);
        });

        // Legend hover: anchor the same tooltip to the horizon's most recent
        // dot (highlightKey enlarges all of that horizon's dots alongside it).
        legendTip.current = (horizon) => {
            const dots = horizon ? pastPointMeta.filter(pm => pm.horizon === horizon) : [];
            const m = dots.length ? dots.reduce((a, b) => (a.asOf > b.asOf ? a : b)) : undefined;
            const x = m ? chart.timeScale().timeToCoordinate(m.asOf as Time) : null;
            const y = m ? m.series.priceToCoordinate(m.price) : null;
            if (!m || x == null || y == null) { tip.style.display = 'none'; return; }
            renderTip(m, x, y);
        };

        chart.timeScale().fitContent();

        return () => {
            seriesByKey.current = {};
            legendTip.current = null;
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
            onMouseEnter={() => { highlightKey(k.id); legendTip.current?.(k.id); }}
            onMouseLeave={() => { highlightKey(null); legendTip.current?.(null); }}
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
                            {GRADE_LABEL[g] ?? g}
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
