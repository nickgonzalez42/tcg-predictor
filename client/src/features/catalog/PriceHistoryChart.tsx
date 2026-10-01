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
    printing?: string;
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
// 1m/6m/12m only (1w is pipeline-internal; the weekly model was retired
// permanently 2026-09-29 — twice-weekly crawls ended daily price accrual).
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
function monthlyIncrements(candidates: PastForecast[], stepDays = 28): PastForecast[] {
    const dated = candidates.filter(f => f.asOf);
    if (dated.length <= 1) return dated;
    const STEP = stepDays * 86400e3;
    const HALF_STEP = (stepDays / 2) * 86400e3;
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

// stepDays: how densely a horizon view tiles its dots — the 1M range view
// uses 7-day slots (nightly cohorts support up to daily), wider views 28.
function pickPastForecasts(past: PastForecast[], grade: string, view: string,
                           stepDays = 28): PastForecast[] {
    if (view !== 'latest')
        return monthlyIncrements(past.filter(f => f.target === grade && f.horizon === view), stepDays);
    return PAST_HORIZONS.flatMap(horizon => {
        const candidates = past.filter(f => f.target === grade && f.horizon === horizon);
        if (!candidates.length) return [];
        // Month-bucket cohorts (graded tiers) issue a forecast every day that
        // all aim at the same next-month date, so "latest targetDate" alone is
        // an 18-way tie. Break it toward the EARLIEST issue: the dot shown is
        // the full-horizon forecast, not one made the day before it landed.
        const issued = (f: PastForecast) => f.asOf ?? f.issuedAt ?? '9999-12-31';
        const latest = candidates.reduce((m, f) => (f.targetDate > m ? f.targetDate : m),
                                         candidates[0].targetDate);
        return [candidates
            .filter(f => f.targetDate === latest)
            .reduce((a, b) => (issued(a) <= issued(b) ? a : b))];
    });
}

export default function PriceHistoryChart({ game, id, printing, forecasts }: Props) {
    const { data, isLoading } = useFetchCardHistoryQuery({ game, id, printing });
    const { data: pastData } = useFetchCardForecastHistoryQuery({ game, id, printing });
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
    // User's zoom/pan survives effect rebuilds (2026-08-27: a click re-renders
    // the parent, new prop identities re-run the effect, and the rebuilt chart
    // snapped back to the tab window — reported as "click resets the zoom").
    // Cleared only when the user intentionally changes view (grade/range tab).
    const savedRange = useRef<{ from: Time; to: Time } | null>(null);
    const viewKey = useRef('');

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

        // Mobile pass (2026-09-13): narrow screens get a shorter chart, a
        // history-dominated 1M window, bigger touch targets, and a docked
        // tooltip; coarse pointers get bigger dots and a fatter hit radius.
        const narrow = el.clientWidth < 520;
        const coarse = typeof window !== 'undefined'
            && !!window.matchMedia?.('(pointer: coarse)').matches;
        const dotR = coarse ? 5 : 3;
        // Source per date, for the line tooltip and the source-era overlay
        // (2026-09-13 honesty pass: PriceCharting-era history and injected
        // forecast-base points must be tellable from daily TCGplayer market
        // data — a mixed line with no provenance reads as broken).
        const sourceByDate = new Map<string, string>(
            all.map(p => [p.date, (p as { source?: string }).source ?? 'tcgplayer']));

        // Level-of-detail tiers (2026-08-22): the served series mixes monthly
        // history with the daily fleet tail, which made the last month read as
        // a different, denser line than the rest. The DRAWN line is uniform —
        // one cadence for the whole visible window — and zooming swaps tiers,
        // so daily detail is on demand instead of always-on.
        //   > ~14 months visible: monthly (last point per calendar month)
        //   ~3.5-14 months:       weekly  (last point per 7-day slot)
        //   under ~3.5 months:    daily   (every served point)
        const lastDate = all[all.length - 1].date;
        const day0 = Date.parse(lastDate);
        // The range tabs set the visible WINDOW (zoom then roams freely over
        // the full series); dots outside the window no longer stretch the axis
        // because nothing calls fitContent on a windowed view.
        const months = RANGES.find(r => r.key === range)?.months;
        const cutoff = months ? addMonths(lastDate, -months) : null;

        // Past forecasts are picked BEFORE the line tiers are built: every
        // visible dot's true context — the price on its generation day and
        // the price that landed on its target day — is injected into EVERY
        // tier, so a downsampled line still passes through the exact prices
        // a forecast is judged against (2026-08-27: on the monthly tier the
        // line read ~$113 where a forecast's real base was $38).
        const dotStep = range === '1m' ? 7 : 28;
        const pastPicks = pickPastForecasts(pastData?.forecasts ?? [], grade, pastView, dotStep)
            .filter(p => !hidden.has(p.horizon))
            .filter(p => p.asOf && (!cutoff || p.targetDate >= cutoff));
        const inject = new Map<string, number>();
        for (const p of pastPicks) {
            const t = p.issuedAt ?? p.asOf;
            if (t && p.basePrice != null && t < p.targetDate) inject.set(t, p.basePrice);
            if (p.realizedPrice != null && p.targetDate <= lastDate)
                inject.set(p.targetDate, p.realizedPrice);
        }
        // Injected points that aren't real series dates get their own muted
        // markers + a tooltip label, so a cross-era forecast base can't
        // masquerade as a market print.
        const injectedOnly = [...inject].filter(([d]) => !sourceByDate.has(d));
        for (const [d] of injectedOnly) sourceByDate.set(d, 'forecast reference');
        const bucketLast = (keyOf: (d: string) => string) => {
            const m = new Map<string, typeof all[number]>();
            for (const p of all) m.set(keyOf(p.date), p);   // later points win
            return [...m.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
        };
        const enrich = (pts: typeof all) => {
            const have = new Set(pts.map(p => p.date));
            const extra = [...inject]
                .filter(([d]) => !have.has(d))
                .map(([date, price]) => ({ ...pts[0], date, price }));
            return extra.length ? [...pts, ...extra].sort((a, b) => (a.date < b.date ? -1 : 1)) : pts;
        };
        const tiers = {
            daily: enrich(all),
            weekly: enrich(bucketLast(d => String(Math.floor((day0 - Date.parse(d)) / (7 * 86400e3))))),
            monthly: enrich(bucketLast(d => d.slice(0, 7))),
        };
        const tierFor = (spanDays: number): keyof typeof tiers =>
            spanDays <= 105 ? 'daily' : spanDays <= 420 ? 'weekly' : 'monthly';

        // Same view as last build -> restore the user's zoom; a real tab or
        // grade change starts fresh from the tab window.
        const key = grade + '|' + range;
        const keep = viewKey.current === key ? savedRange.current : null;
        viewKey.current = key;
        if (!keep) savedRange.current = null;
        const spanFrom = keep ? String(keep.from) : (cutoff ?? all[0].date);
        const spanTo = keep ? Date.parse(String(keep.to)) : day0;
        let tier = tierFor((spanTo - Date.parse(spanFrom)) / 86400e3);
        const points = tiers[tier];
        if (!points.length) return;

        // Theme colors come from the CSS variables so both palettes stay in sync.
        const css = getComputedStyle(el);
        const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
        const history = v('--chart-history', '#3d7dca');
        const forecastColor = v('--chart-forecast', '#e0b000');
        const textMuted = v('--text-muted', '#8b96ad');
        const border = v('--border', '#2e3a52');

        const chart = createChart(el, {
            height: narrow ? 260 : 340,
            autoSize: true,
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: textMuted },
            grid: { vertLines: { color: border }, horzLines: { color: border } },
            rightPriceScale: { borderColor: border },
            timeScale: {
                borderColor: border,
                // The daily whitespace grid (below) can put thousands of
                // slots on a multi-year card; the default 0.5px minimum bar
                // width would stop "ALL" from fitting on narrow charts.
                minBarSpacing: 0.05,
                // Uniform tick labels (2026-10-01, user request): always
                // "Sep 29". The library's default mixes "Sep", "29" and
                // "2027" marks, which read as equal spans side by side.
                tickMarkFormatter: (t: Time) =>
                    new Date(String(t) + 'T00:00:00Z').toLocaleDateString('en-US',
                        { month: 'short', day: 'numeric', timeZone: 'UTC' }),
            },
            // Zoom is a first-class control (2026-08-22): wheel zooms around
            // the cursor, pinch zooms on touch, horizontal drag pans. Vertical
            // touch drag stays OFF so the page still scrolls over the chart.
            handleScroll: { pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false, mouseWheel: false },
            handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
        });
        seriesByKey.current = {};
        const track = (key: string, s: ISeriesApi<SeriesType>) =>
            (seriesByKey.current[key] ??= []).push(s);

        let historySeries: ISeriesApi<SeriesType> | null = null;
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
            historySeries = series;
        }

        // Source-era overlay: the stretch of the line that comes from
        // PriceCharting sales (pre-cutover / bridge) draws a muted line on
        // top, so the era boundary is visible instead of silently blended.
        // Only when the series is actually mixed-source: graded tiers are
        // 100% PriceCharting by design, and an "era boundary" overlay with no
        // boundary just paints the whole line gray (the per-point tooltip
        // still names the source).
        const mixedSource = all.some(p => sourceByDate.get(p.date) === 'tcgplayer');
        const pcSpan = points.filter(p => {
            const s = sourceByDate.get(p.date);
            return s && s !== 'tcgplayer' && s !== 'forecast reference';
        });
        let pcOverlay: ISeriesApi<SeriesType> | null = null;
        if (mixedSource && pcSpan.length > 1 && !hidden.has('history')) {
            pcOverlay = chart.addSeries(LineSeries, {
                color: textMuted,
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            pcOverlay.setData(pcSpan.map(p => ({ time: p.date, value: p.price })));
        }

        // Injected forecast-base/realized points: muted standalone markers.
        if (injectedOnly.length && !hidden.has('history')) {
            const inj = chart.addSeries(LineSeries, {
                color: textMuted,
                lineVisible: false,
                pointMarkersVisible: true,
                pointMarkersRadius: coarse ? 3.5 : 2.5,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            inj.setData(injectedOnly
                .map(([date, price]) => ({ time: date, value: price }))
                .sort((a, b) => (a.time < b.time ? -1 : 1)));
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
        // A past forecast's anchor is its own record: the price it was
        // computed FROM (basePrice) on the day it was generated (issuedAt).
        // Do NOT snap onto the drawn price line — sparse series interpolate
        // between distant points, so the line's height on the issue date can
        // be far from the true generation-day price, and snapping drew the
        // trajectory from a price the forecast never saw (user report
        // 2026-08-14). If the tail floats off the line, the LINE is the
        // approximation there, not the anchor.
        const anchorOf = (p: { asOf?: string, issuedAt?: string, basePrice?: number, targetDate: string }) => {
            // Anchor must sit STRICTLY BEFORE the dot on the time axis —
            // descending setData times throw and kill every series.
            const t = p.issuedAt ?? p.asOf;
            return t && p.basePrice != null && t < p.targetDate
                ? { time: t, value: p.basePrice } : null;
        };
        type PointMeta = {
            series: ISeriesApi<SeriesType>; horizon: string; asOf: string; issuedAt?: string;
            targetDate: string; price: number; realizedPrice?: number;
            anchorTime?: string; anchorValue?: number;
        };
        const pastPointMeta: PointMeta[] = [];
        // pastPicks computed above, before the tiers — its anchor/realized
        // prices are injected into every tier's line.
        for (const p of pastPicks) {
            // Permanently inkless anchor holding this forecast's generation
            // date (and base price) on the time axis. The hover trajectory is
            // drawn by ONE shared overlay series below — but if these times
            // only appeared when hovered, the index-based axis would re-space
            // mid-hover and slide the dots out from under the cursor.
            const a = anchorOf(p);
            if (a) {
                const anchor = chart.addSeries(LineSeries, {
                    lineVisible: false,
                    pointMarkersVisible: false,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                });
                anchor.setData([a, { time: p.targetDate, value: p.forecastPrice }]);
            }
            const dot = chart.addSeries(LineSeries, {
                color: pastColors[p.horizon] ?? '#c678dd',
                lineVisible: false,          // markers only — no connecting line
                pointMarkersVisible: true,
                pointMarkersRadius: dotR,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
            });
            dot.setData([{ time: p.targetDate, value: p.forecastPrice }]);
            track(p.horizon, dot);
            pastPointMeta.push({ series: dot, horizon: p.horizon, asOf: p.asOf!, issuedAt: p.issuedAt,
                                 targetDate: p.targetDate, price: p.forecastPrice,
                                 realizedPrice: p.realizedPrice,
                                 anchorTime: a?.time, anchorValue: a?.value });
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
            if (!m || m.anchorTime == null || m.anchorValue == null) { traj.setData([]); return; }
            traj.applyOptions({ color: pastColors[m.horizon] ?? '#c678dd' });
            traj.setData([{ time: m.anchorTime, value: m.anchorValue },
                          { time: m.targetDate, value: m.price }]);
        };

        // Hover (desktop) / tap (touch) tooltip for the past-forecast dots.
        // lightweight-charts fires crosshair moves for taps too, so one handler
        // covers both. seriesData only carries a dot's series at its own time.
        el.style.position = 'relative';
        const tip = document.createElement('div');
        tip.className = 'chart-tip' + (narrow ? ' chart-tip--docked' : '');
        tip.style.display = 'none';
        el.appendChild(tip);
        const fmtDate = (d: string) =>
            new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });

        const renderTip = (m: PointMeta, x: number, y: number) => {
            // Fulfilled dots show the forecast NEXT TO the price that actually
            // landed on that date — the miss in dollars, not just as a visual
            // gap. Pending dots say when they land instead.
            const outcome = m.realizedPrice != null
                ? `<span>actual $${m.realizedPrice.toFixed(2)} on ${fmtDate(m.targetDate)}</span>`
                : `<span>lands ${fmtDate(m.targetDate)}</span>`;
            tip.innerHTML =
                `<strong>${HORIZON_LABEL[m.horizon] ?? m.horizon} forecast</strong>` +
                `<span>generated ${fmtDate(m.issuedAt ?? m.asOf)}</span>` +
                `<span>$${m.price.toFixed(2)}</span>` +
                outcome;
            tip.style.display = 'flex';   // matches .chart-tip's column layout — 'block' would collapse the lines
            if (narrow) return;           // docked: CSS pins it below the plot
            const left = Math.min(Math.max(x + 12, 4), el.clientWidth - tip.offsetWidth - 4);
            tip.style.left = `${left}px`;
            tip.style.top = `${Math.max(y - tip.offsetHeight - 10, 4)}px`;
        };

        // Line readout (2026-09-13): hovering/tapping the price line itself
        // shows date, price, and PROVENANCE — TCGplayer market, PriceCharting
        // sales, or an injected forecast-reference point.
        const SOURCE_LABEL: Record<string, string> = {
            tcgplayer: 'TCGplayer market price',
            pricecharting: 'PriceCharting sales',
            'forecast reference': 'forecast reference point',
        };
        const renderLineTip = (time: string, price: number, x: number, y: number) => {
            const src = sourceByDate.get(time);
            tip.innerHTML =
                `<strong>$${price.toFixed(2)}</strong>` +
                `<span>${fmtDate(time)}</span>` +
                (src ? `<span>${SOURCE_LABEL[src] ?? src}</span>` : '');
            tip.style.display = 'flex';
            if (narrow) return;
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
            const hitR = coarse ? 34 : 20;
            if (!best || bestD > hitR) {
                // No dot under the pointer — fall back to the price line's
                // own readout at the hovered time.
                showLink(null);
                const t = param.time != null ? String(param.time) : null;
                const sd = historySeries ? param.seriesData.get(historySeries) : undefined;
                const price = (sd as { value?: number } | undefined)?.value;
                if (t && price != null) {
                    const ly = historySeries!.priceToCoordinate(price) ?? param.point.y;
                    renderLineTip(t, price, param.point.x, ly);
                } else {
                    tip.style.display = 'none';
                }
                return;
            }
            const y = best.series.priceToCoordinate(best.price) ?? param.point.y;
            renderTip(best, param.point.x, y);
            showLink(best);
        });

        // Initial window: the user's preserved zoom if this is a prop-churn
        // rebuild, else the tab window. The tab window's right edge includes
        // the dashed forecast chain (it can reach a year past the last real
        // point), matching the old fitContent.
        const chainEnd = chainPts.length > 1 ? String(chainPts[chainPts.length - 1].time) : lastDate;

        // Time-proportional axis (2026-10-01): lightweight-charts spaces
        // BARS equally, not time — a monthly point, a lone daily print and a
        // next-month forecast each took one slot, so "Sep", "29" and "Oct"
        // read as equal gaps. This invisible series owns a slot for EVERY
        // calendar day from the first drawn point through the forecast
        // chain's end; the real series stay sparse and draw across the
        // whitespace, so horizontal distance equals elapsed time in every
        // tier and at every zoom.
        const grid = chart.addSeries(LineSeries, {
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        {
            const gridEnd = Date.parse(chainEnd > lastDate ? chainEnd : lastDate);
            const days: { time: Time }[] = [];
            for (let t = Date.parse(all[0].date); t <= gridEnd; t += 86400e3)
                days.push({ time: new Date(t).toISOString().slice(0, 10) as Time });
            grid.setData(days);
        }

        let windowEnd = chainEnd > lastDate ? chainEnd : lastDate;
        // Narrow screens, 1M tab: cap the window ~4 weeks past the last real
        // point so history isn't squeezed into a quarter of the plot by the
        // year-long forecast chain (pan right to see the rest of it).
        if (narrow && range === '1m') {
            const cap = addDays(lastDate, 28);
            if (cap < windowEnd) windowEnd = cap;
        }
        if (keep) chart.timeScale().setVisibleRange(keep);
        else if (cutoff) chart.timeScale().setVisibleRange({ from: cutoff as Time, to: windowEnd as Time });
        else chart.timeScale().fitContent();

        chart.timeScale().subscribeVisibleTimeRangeChange(r => {
            if (!r) return;
            savedRange.current = { from: r.from, to: r.to };
            if (!historySeries) return;
            const span = (Date.parse(String(r.to)) - Date.parse(String(r.from))) / 86400e3;
            const want = tierFor(span);
            if (want === tier) return;
            tier = want;
            const vr = chart.timeScale().getVisibleRange();
            historySeries.setData(tiers[tier].map(p => ({ time: p.date, value: p.price })));
            if (pcOverlay) pcOverlay.setData(tiers[tier]
                .filter(p => {
                    const s = sourceByDate.get(p.date);
                    return s && s !== 'tcgplayer' && s !== 'forecast reference';
                })
                .map(p => ({ time: p.date, value: p.price })));
            if (vr) chart.timeScale().setVisibleRange(vr);   // setData must not jump the viewport
        });

        return () => {
            seriesByKey.current = {};
            tip.remove();
            chart.remove();
        };
    }, [data, grade, range, forecasts, pastData, hidden, pastView]);

    if (isLoading) return <div>Loading chart…</div>;
    if (!grades.length) return <div className="est-note">No price history yet for this card.</div>;

    const hasForecast = (forecasts ?? []).some(f => f.target === grade);
    const pastPicks = pickPastForecasts(pastData?.forecasts ?? [], grade, pastView,
                                        range === '1m' ? 7 : 28);
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
                            className={`btn btn--outline range-tab${g === grade ? ' btn--active' : ''}`}
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
