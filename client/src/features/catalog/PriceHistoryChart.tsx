import { useEffect, useRef, useState } from "react";
import { createChart, AreaSeries, LineSeries, ColorType, LineStyle } from "lightweight-charts";
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

function addDays(date: string, days: number) {
    const d = new Date(date + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
}

// Where each forecast horizon lands on the time axis, from the last real point.
const HORIZON_OFFSET: Record<string, (date: string) => string> = {
    '1w': d => addDays(d, 7),
    '1m': d => addMonths(d, 1),
    '6m': d => addMonths(d, 6),
    '12m': d => addMonths(d, 12),
};

// One reviewable point per horizon: the matured archived forecast whose
// target date is nearest to today — the ideal is one targeting exactly now
// (issued one horizon ago), but if none exists the next-closest older one is
// shown instead. No matured forecast at all = no point.
const PAST_HORIZONS = ['1w', '1m', '6m', '12m'];

function pickPastForecasts(past: PastForecast[], grade: string): PastForecast[] {
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

    const grades = data ? GRADE_ORDER.filter(g => data.series[g]?.length) : [];

    // default to the first available tier once data arrives
    useEffect(() => {
        if (grades.length && !grades.includes(grade)) setGrade(grades[0]);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data]);

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
        const series = chart.addSeries(AreaSeries, {
            lineColor: history,
            topColor: 'rgba(61, 125, 202, 0.30)',
            bottomColor: 'rgba(61, 125, 202, 0.02)',
            lineWidth: 2,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
        series.setData(points.map(p => ({ time: p.date, value: p.price })));

        // Dashed gold continuation: last real point -> the model's forecasts for
        // this tier at every horizon (1w, 1m, 6m, 12m).
        const tierFc = (forecasts ?? [])
            .filter(f => f.target === grade && HORIZON_OFFSET[f.horizon]);
        if (tierFc.length) {
            const last = points[points.length - 1];
            const fcPoints = [
                { time: last.date, value: last.price },
                ...tierFc
                    .map(f => ({ time: HORIZON_OFFSET[f.horizon](last.date), value: f.forecastPrice }))
                    .sort((a, b) => a.time.localeCompare(b.time)),
            ];
            const fcSeries = chart.addSeries(LineSeries, {
                color: forecastColor,
                lineWidth: 2,
                lineStyle: LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            fcSeries.setData(fcPoints);
        }

        // Past-forecast review: for each horizon, the archived prediction that
        // was aiming at (roughly) today, drawn as a lone dot at its target
        // date so it can be eyeballed against the actual line. One tiny series
        // per point because two horizons can mature on the same date.
        const pastColor = v('--chart-past-forecast', '#c678dd');
        for (const p of pickPastForecasts(pastData?.forecasts ?? [], grade)) {
            const dot = chart.addSeries(LineSeries, {
                color: pastColor,
                lineVisible: false,
                pointMarkersVisible: true,
                pointMarkersRadius: 4,
                priceLineVisible: false,
                lastValueVisible: false,
                title: `${p.horizon.toUpperCase()} fcst`,
            });
            dot.setData([{ time: p.targetDate, value: p.forecastPrice }]);
        }

        chart.timeScale().fitContent();

        return () => chart.remove();
    }, [data, grade, range, forecasts, pastData]);

    if (isLoading) return <div>Loading chart…</div>;
    if (!grades.length) return <div className="est-note">No price history yet for this card.</div>;

    const hasForecast = (forecasts ?? []).some(f => f.target === grade);
    const hasPast = pickPastForecasts(pastData?.forecasts ?? [], grade).length > 0;

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
            <div className="mono" style={{ marginTop: '6.4px' }}>
                solid blue = history{hasForecast ? ' · dashed gold = model forecast' : ''}
                {hasPast ? ' · purple dots = past forecasts, shown at the date they predicted' : ''}
            </div>
        </div>
    );
}
