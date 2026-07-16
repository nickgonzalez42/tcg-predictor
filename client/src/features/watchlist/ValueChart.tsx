import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, AreaSeries, LineSeries, LineStyle, ColorType, type ISeriesApi } from "lightweight-charts";
import { useFetchPortfolioSummaryQuery, type PortfolioSummary } from "./watchlistApi";
import ChangePill from "../../app/shared/components/ChangePill";
import { currencyFormat, shortDate } from "../../lib/util";
import { GAMES } from "../../lib/games";

const RANGES: { key: string; label: string; months?: number }[] = [
    { key: '1m', label: '1M', months: 1 },
    { key: '6m', label: '6M', months: 6 },
    { key: '1y', label: '1Y', months: 12 },
    { key: 'all', label: 'ALL' },
];

// "#rrggbb" -> rgba() at the given opacity (non-hex values pass through).
const fade = (hex: string, alpha: number) => {
    const m = /^#?([\da-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};

// Portfolio value-over-time chart: green collection line (zero until each
// copy's add date) vs a dashed S&P 500 what-if line (the same dollars put
// into SPX on the same days), both from account creation. Hovering a legend
// entry dims the other series.
export default function ValueChart({ summary }: { summary: PortfolioSummary }) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [range, setRange] = useState('all');
    // Game chips: the chart (and its stats) can narrow to one game's copies.
    // 'all' reuses the page's summary; a game fetches its own filtered rollup.
    const [game, setGame] = useState('all');
    const { data: gameSummary } = useFetchPortfolioSummaryQuery(game, { skip: game === 'all' });
    const s = game === 'all' ? summary : gameSummary;
    const hasBench = !!s?.benchmark?.length;

    // Live series handles + their base colors, for the legend-hover highlight
    // (applyOptions directly; no state, so the chart isn't rebuilt).
    const seriesRef = useRef<{
        area?: ISeriesApi<'Area'>; spx?: ISeriesApi<'Line'>;
        up?: string; spxColor?: string;
    }>({});

    const highlight = (target: 'collection' | 'spx' | null) => {
        const s = seriesRef.current;
        if (!s.area || !s.up) return;
        const dimArea = target === 'spx', dimSpx = target === 'collection';
        s.area.applyOptions({
            lineColor: dimArea ? fade(s.up, 0.25) : s.up,
            topColor: fade(s.up, dimArea ? 0.06 : 0.25),
            bottomColor: fade(s.up, 0.02),
        });
        if (s.spx && s.spxColor)
            s.spx.applyOptions({ color: dimSpx ? fade(s.spxColor, 0.22) : s.spxColor });
    };

    // Date cutoff (not a point count): the collection axis mixes monthly and
    // add dates, and the benchmark is daily. Memoized so the chart effect and
    // the stats strip share one slice per (summary, range).
    const { points, bench } = useMemo(() => {
        const months = RANGES.find(r => r.key === range)?.months;
        const cutoff = months
            ? new Date(Date.now() - months * 30.44 * 86400e3).toISOString().slice(0, 10)
            : null;
        const slice = (pts?: { date: string; value: number }[]) =>
            (cutoff ? pts?.filter(p => p.date >= cutoff) : pts) ?? [];
        return { points: slice(s?.series), bench: slice(s?.benchmark) };
    }, [s, range]);

    useEffect(() => {
        const el = containerRef.current;
        if (!el || points.length < 2) return;

        const css = getComputedStyle(el);
        const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
        const up = v('--up', '#3fd98a');
        const textMuted = v('--text-muted', '#8b96ad');
        const border = v('--border', '#2e3a52');

        const chart = createChart(el, {
            height: 240,
            autoSize: true,
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: textMuted },
            grid: { vertLines: { color: border }, horzLines: { color: border } },
            rightPriceScale: { borderColor: border },
            timeScale: { borderColor: border },
            handleScroll: false,
            handleScale: false,
        });
        const series = chart.addSeries(AreaSeries, {
            lineColor: up,
            topColor: 'rgba(63, 217, 138, 0.25)',
            bottomColor: 'rgba(63, 217, 138, 0.02)',
            lineWidth: 2,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
        series.setData(points.map(p => ({ time: p.date, value: p.value })));
        seriesRef.current = { area: series, up };

        if (bench.length >= 2) {
            const spxColor = v('--link', '#7fb0ea');
            const spxLine = chart.addSeries(LineSeries, {
                color: spxColor,
                lineWidth: 2,
                lineStyle: LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
                priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
            });
            spxLine.setData(bench.map(p => ({ time: p.date, value: p.value })));
            seriesRef.current.spx = spxLine;
            seriesRef.current.spxColor = spxColor;
        }

        chart.timeScale().fitContent();

        return () => {
            seriesRef.current = {};
            chart.remove();
        };
    }, [points, bench]);

    if (!summary.series?.length) return null;

    // Stats strip: each line's current value, plus its APPRECIATION over the
    // selected range — the value change minus what was contributed (each
    // copy's cost basis on its add date), so adding cards isn't "gain".
    // % = appreciation over the money at work in the window.
    const first = points[0], last = points[points.length - 1];
    const bFirst = bench[0], bLast = bench[bench.length - 1];
    const investedAt = (d?: string) =>
        d ? (s?.invested?.filter(p => p.date <= d).at(-1)?.value ?? 0) : 0;
    const contributed = last ? investedAt(last.date) - (first ? investedAt(first.date) : 0) : 0;
    const appreciation = (from?: { value: number }, to?: { value: number }) =>
        to ? to.value - (from?.value ?? 0) - contributed : null;
    const apprecPct = (from?: { value: number }, to?: { value: number }) => {
        const a = appreciation(from, to);
        const base = (from?.value ?? 0) + contributed;
        return a != null && base > 0 ? (a / base) * 100 : null;
    };
    // Whole-life figures (not range-scoped): total money in, and unrealized
    // P/L against it. Peak follows the selected range.
    const investedTotal = investedAt(last?.date);
    const plUsd = last ? last.value - investedTotal : null;
    const plPct = plUsd != null && investedTotal > 0 ? (plUsd / investedTotal) * 100 : null;
    const peak = points.reduce<{ date: string; value: number } | null>(
        (m, p) => (m == null || p.value > m.value ? p : m), null);

    return (
        <div className="panel detail-panel">
            <div className="chart-tabs">
                {/* Game picker (left) and timeframe chips (right) share one row;
                    .chart-tabs space-between splits them. */}
                <select className="input chart-game" aria-label="Game" value={game}
                    onChange={e => setGame(e.target.value)}>
                    {[{ value: 'all', label: 'All games' }, ...GAMES].map(g => (
                        <option key={g.value} value={g.value}>{g.label}</option>
                    ))}
                </select>
                <div className="range-tabs" role="group" aria-label="Time range">
                    {RANGES.map(r => (
                        <button key={r.key}
                            className={`btn btn--outline range-tab${r.key === range ? ' btn--active' : ''}`}
                            onClick={() => setRange(r.key)}>
                            {r.label}
                        </button>
                    ))}
                </div>
            </div>
            {hasBench && (
                <div className="chart-legend mono">
                    <span className="chart-legend__item"
                        onMouseEnter={() => highlight('collection')}
                        onMouseLeave={() => highlight(null)}>
                        <i className="chart-legend__swatch" /> Collection
                    </span>
                    <span className="chart-legend__item"
                        onMouseEnter={() => highlight('spx')}
                        onMouseLeave={() => highlight(null)}
                        title="What the same money would be worth if each card's cost had gone into the S&P 500 on the day you added it">
                        <i className="chart-legend__swatch chart-legend__swatch--dashed" /> S&amp;P 500 (same $ invested)
                    </span>
                </div>
            )}
            <div ref={containerRef} style={{ width: '100%' }} />
            {last && (
                <div className="chart-stats">
                    <div className="chart-stat">
                        <span className="chart-stat__label mono">Collection</span>
                        <span className="chart-stat__value">{currencyFormat(last.value)}</span>
                        <span className="chart-stat__delta">
                            <ChangePill value={appreciation(first, last)} unit="usd"
                                title="Appreciation over the selected range (cards you added don't count as gains)" />
                            <ChangePill value={apprecPct(first, last)}
                                title="Appreciation relative to the money at work in this range" />
                        </span>
                    </div>
                    {bLast && (
                        <>
                            <div className="chart-stat">
                                <span className="chart-stat__label mono">S&amp;P 500 (same $)</span>
                                <span className="chart-stat__value">{currencyFormat(bLast.value)}</span>
                                <span className="chart-stat__delta">
                                    <ChangePill value={appreciation(bFirst, bLast)} unit="usd"
                                        title="What the S&P would have gained on the same money (contributions excluded)" />
                                    <ChangePill value={apprecPct(bFirst, bLast)}
                                        title="S&P appreciation relative to the money at work in this range" />
                                </span>
                            </div>
                            <div className="chart-stat">
                                <span className="chart-stat__label mono">Vs market</span>
                                <span className="chart-stat__value">
                                    {last.value >= bLast.value ? 'Ahead' : 'Behind'}
                                </span>
                                <span className="chart-stat__delta">
                                    <ChangePill value={last.value - bLast.value} unit="usd"
                                        title="Collection value minus what the same money in the S&P 500 would be worth today" />
                                    <ChangePill value={bLast.value > 0 ? (last.value / bLast.value - 1) * 100 : null}
                                        title="Collection value relative to the S&P what-if" />
                                </span>
                            </div>
                        </>
                    )}
                    <div className="chart-stat">
                        <span className="chart-stat__label mono">Invested</span>
                        <span className="chart-stat__value">{currencyFormat(investedTotal)}</span>
                    </div>
                    {plUsd != null && (
                        <div className="chart-stat">
                            <span className="chart-stat__label mono">Overall P/L</span>
                            <span className="chart-stat__value"
                                style={{ color: plUsd >= 0 ? 'var(--up)' : 'var(--down)' }}>
                                {plUsd >= 0 ? '+' : '−'}{currencyFormat(Math.abs(plUsd))}
                            </span>
                            <span className="chart-stat__delta">
                                <ChangePill value={plPct} title="Unrealized P/L vs total invested" />
                            </span>
                        </div>
                    )}
                    {peak && (
                        <div className="chart-stat">
                            <span className="chart-stat__label mono">Peak value</span>
                            <span className="chart-stat__value">{currencyFormat(peak.value)}</span>
                            <span className="chart-stat__delta">
                                <span className="mono est-note">{shortDate(peak.date)}</span>
                            </span>
                        </div>
                    )}
                </div>
            )}
            {hasBench && (
                <p className="est-note chart-note">
                    S&amp;P comparison invests each card's purchase price on the day you added
                    it; cards without a purchase price use their market price (at their
                    grade) on that day.
                </p>
            )}
        </div>
    );
}
