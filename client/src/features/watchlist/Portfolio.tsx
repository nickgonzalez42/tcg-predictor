import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { createChart, AreaSeries, LineSeries, LineStyle, ColorType, type ISeriesApi } from "lightweight-charts";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import {
    useFetchTrackedCardsQuery,
    useFetchPortfolioSummaryQuery,
    useAddToWatchlistMutation,
    useRemoveOwnedCopyMutation,
    type PortfolioSummary,
} from "./watchlistApi";
import { ownedParamsSlice } from "./trackedParamsSlice";
import { OwnedCopyRow } from "./OwnedConditionItem";
import { tierLabel } from "./grades";
import { trackedSortGroups } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import SortTh from "../../app/shared/components/SortTh";
import CardThumbCell from "../../app/shared/components/CardThumbCell";
import TrackedFilters from "./TrackedFilters";
import { useFetchFiltersQuery } from "../catalog/catalogApi";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import CardLoader from "../../app/shared/components/CardLoader";
import type { Card } from "../../app/models/card";
import { usePageMeta } from "../../lib/usePageMeta";
import { GAMES } from "../../lib/games";
import Modal from "../../app/shared/components/Modal";


const RANGES: { key: string; label: string; months?: number }[] = [
    { key: '1m', label: '1M', months: 1 },
    { key: '6m', label: '6M', months: 6 },
    { key: '1y', label: '1Y', months: 12 },
    { key: 'all', label: 'ALL' },
];

const DONUT_COLORS: Record<string, string> = {
    // games
    'One Piece': '#3d7dca',
    'Pokémon': '#ffcb05',
    'Yu-Gi-Oh!': '#c678dd',
    'Magic': '#ff9e64',
    'Lorcana': '#4dd0e1',
    'Digimon': '#3fd98a',
    'Gundam': '#f06292',
    // condition tiers
    'Ungraded': '#3d7dca',
    'Grade 7': '#8b96ad',
    'Grade 8': '#4dd0e1',
    'Grade 9': '#ff9e64',
    'Grade 9.5': '#c678dd',
    'PSA 10': '#3fd98a',
    'BGS 10': '#ffcb05',
    'CGC 10': '#f06292',
    'SGC 10': '#e0e6f0',
};

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
function ValueChart({ summary }: { summary: PortfolioSummary }) {
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
                <div className="chart-tabs__right">
                    <div className="range-tabs" role="group" aria-label="Game">
                        {[{ value: 'all', label: 'ALL' }, ...GAMES].map(g => (
                            <button key={g.value}
                                className={`btn btn--outline range-tab${g.value === game ? ' btn--active' : ''}`}
                                onClick={() => setGame(g.value)}>
                                {g.label.toUpperCase()}
                            </button>
                        ))}
                    </div>
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

// SVG donut of one allocation breakdown (by game, or by condition tier).
function AllocationDonut({ title, slices }: {
    title: string; slices: { label: string; value: number; pct: number }[];
}) {
    if (!slices.length) return null;

    const R = 40, C = 2 * Math.PI * R;
    let offset = 0;

    return (
        <div className="panel detail-panel">
            <span className="mono detail-panel__title">{title}</span>
            <div className="donut">
                <svg viewBox="0 0 100 100" className="donut__svg" aria-hidden="true">
                    {slices.map(s => {
                        const len = (s.pct / 100) * C;
                        const el = (
                            <circle
                                key={s.label}
                                cx="50" cy="50" r={R}
                                fill="none"
                                stroke={DONUT_COLORS[s.label] ?? '#8b96ad'}
                                strokeWidth="14"
                                strokeDasharray={`${len} ${C - len}`}
                                strokeDashoffset={-offset}
                                transform="rotate(-90 50 50)"
                            />
                        );
                        offset += len;
                        return el;
                    })}
                </svg>
                <ul className="donut__legend">
                    {slices.map(s => (
                        <li key={s.label}>
                            <span className="donut__dot" style={{ background: DONUT_COLORS[s.label] ?? '#8b96ad' }} />
                            {s.label} <span className="mono">{s.pct.toFixed(0)}%</span>
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    );
}

function BestWorst({ summary }: { summary: PortfolioSummary }) {
    const tiles = [
        { tag: 'Best position', p: summary.best },
        { tag: 'Worst position', p: summary.worst },
    ].filter(t => t.p);
    if (!tiles.length) return null;

    return (
        <div className="bw-tiles">
            {tiles.map(({ tag, p }) => (
                <Link key={tag} className="panel bw-tile" to={`/catalog/${p!.game}/${p!.id}`}>
                    <span className="mono detail-panel__title">{tag}</span>
                    <img className="bw-tile__thumb" src={p!.pictureUrl} alt="" loading="lazy" />
                    <span className="bw-tile__name">{p!.name}</span>
                    {p!.paid != null && p!.value != null && (
                        <span className="mono bw-tile__values">
                            {currencyFormat(p!.paid)} → {currencyFormat(p!.value)}
                        </span>
                    )}
                    <span className="bw-tile__pills">
                        <ChangePill value={p!.pct} />
                        {p!.plUsd != null && <ChangePill value={p!.plUsd} unit="usd" title="P/L vs paid" />}
                    </span>
                </Link>
            ))}
            <div className="est-note bw-tiles__note">
                Based on copies with a recorded purchase price.
            </div>
        </div>
    );
}

// One position row (a card + condition unit). ✎ expands the per-copy editor inline.
function PositionRow({ card, hasYear }: { card: Card; hasYear: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [addCopy, { isLoading: adding }] = useAddToWatchlistMutation();
    const [removeCopy, { isLoading: removing }] = useRemoveOwnedCopyMutation();

    const copies = card.ownedCopies ?? [];
    const qty = card.ownedQuantity ?? copies.length;
    const grade = card.ownedGrade ?? '';
    const mktValue = card.price != null ? card.price * qty : null;

    const paidCopies = copies.filter(c => c.purchasePrice != null);
    const paid = paidCopies.length ? paidCopies.reduce((s, c) => s + (c.purchasePrice ?? 0), 0) : null;
    // P/L compares only the copies that have a recorded cost.
    const pl = paid != null && card.price != null ? card.price * paidCopies.length - paid : null;

    const addOne = () => addCopy({ game: gameKey(card.game), productId: card.id, kind: 'owned', grade });
    const removeOne = () => {
        const blank = [...copies].reverse().find(c => c.purchasePrice == null && !c.acquiredAt && !c.note);
        const target = blank ?? copies[copies.length - 1];
        if (target) removeCopy({ id: target.id });
    };
    // Removing the LAST copy deletes the whole position — confirm that one.
    const onMinus = () => (qty <= 1 ? setConfirming(true) : removeOne());

    return (
        <>
            {confirming && (
                <Modal title="Remove from portfolio" onClose={() => setConfirming(false)}>
                    <p>
                        This is the last copy of <strong>{card.name}</strong> ({tierLabel(card.ownedGrade)})
                        — removing it deletes the position from your portfolio.
                    </p>
                    <div className="modal__actions">
                        <button className="btn btn--outline" onClick={() => setConfirming(false)}>
                            Cancel
                        </button>
                        <button className="btn btn--danger" disabled={removing}
                            onClick={() => { removeOne(); setConfirming(false); }}>
                            Remove
                        </button>
                    </div>
                </Modal>
            )}
            <tr className="screener__row" onClick={() => setExpanded(v => !v)}>
                <CardThumbCell card={card} />
                <td>
                    <Link className="screener__name" to={`/catalog/${gameKey(card.game)}/${card.id}`}
                        onClick={e => e.stopPropagation()}>
                        {card.name}
                    </Link>
                    <div className="mono">{[card.setName, card.rarity].filter(Boolean).join(' · ')}</div>
                </td>
                <td><span className="owned-condition">{tierLabel(card.ownedGrade)}</span></td>
                <td className="screener__num">{qty}</td>
                <td className="screener__num">{paid != null ? currencyFormat(paid) : '—'}</td>
                <td className="screener__num screener__price">
                    {mktValue != null ? currencyFormat(mktValue) : '—'}
                    {card.priceAsOf && <div className="mono price-asof">{shortDate(card.priceAsOf)}</div>}
                </td>
                <td className="screener__num">
                    {pl != null ? <ChangePill value={pl} unit="usd" title="vs recorded cost" /> : <span className="mono">—</span>}
                </td>
                <td className="screener__num">
                    <ChangePill value={hasYear ? card.fcst12Pct : card.fcst6Pct}
                        title={`${hasYear ? '1 year' : '6 month'} model forecast`} />
                </td>
                <td><Sparkline points={card.sparkline} /></td>
                <td className="screener__actions" onClick={e => e.stopPropagation()}>
                    {/* Row click still expands the copy editor (paid/date/note). */}
                    <button className="btn btn--outline btn--circle" disabled={removing || qty === 0}
                        onClick={onMinus} title="Remove one copy">−</button>
                    <button className="btn btn--outline btn--circle" disabled={adding}
                        onClick={addOne} title="Add one copy">＋</button>
                </td>
            </tr>
            {expanded && (
                <tr className="position-editor">
                    <td colSpan={10}>
                        <div className="owned-copies" style={{ borderTop: 'none', marginTop: 0 }}>
                            <div className="owned-copies__head">
                                Copies at {tierLabel(card.ownedGrade)} — a copy with a paid price,
                                date or note becomes its own position row.
                            </div>
                            {copies.map(copy => (
                                <OwnedCopyRow key={copy.id} copy={copy}
                                    onClose={() => setExpanded(false)} />
                            ))}
                        </div>
                    </td>
                </tr>
            )}
        </>
    );
}

export default function Portfolio() {
    usePageMeta("Portfolio");
    const [showPaidHelp, setShowPaidHelp] = useState(false);
    const { setPageNumber, setOrderBy } = ownedParamsSlice.actions;
    const params = useAppSelector(state => state.ownedParams);
    const dispatch = useAppDispatch();

    const { data, isLoading } = useFetchTrackedCardsQuery({ kind: 'owned', ...params });
    const { data: summary } = useFetchPortfolioSummaryQuery();
    // Young games (digimon/gundam) have no 12m horizon yet — the forecast
    // column falls back to their 6m numbers and relabels itself.
    const { data: filtersData } = useFetchFiltersQuery(params.game);
    const hasYear = filtersData?.hasYear ?? true;

    return (
        <>
            {/* ----- Header: total value + change pills + value chart ----- */}
            <div className="pf-hero">
                <span className="mono">Portfolio value</span>
                <div className="pf-hero__value">
                    {summary ? currencyFormat(summary.totalValue) : '—'}
                </div>
                <div className="pf-hero__pills">
                    {summary?.monthChangeUsd != null && (
                        <ChangePill value={summary.monthChangeUsd} unit="usd" suffix={
                            (summary.monthChangePct != null
                                ? `(${summary.monthChangePct >= 0 ? '+' : '−'}${Math.abs(summary.monthChangePct).toFixed(1)}%) `
                                : '') + 'this month'
                        } />
                    )}
                    {summary?.allTime && (
                        <ChangePill value={summary.allTime.plPct} suffix="vs cost" />
                    )}
                </div>
                {summary && <ValueChart summary={summary} />}
            </div>

            {/* ----- Right rail: allocation + best/worst ----- */}
            <div className="pf-side">
                {summary && <AllocationDonut title="Allocation · games" slices={summary.allocation ?? []} />}
                {summary && <AllocationDonut title="Allocation · grades" slices={summary.gradeAllocation ?? []} />}
                {summary && <BestWorst summary={summary} />}
            </div>

            {/* ----- Positions table ----- */}
            <div className="pf-positions full-span">
                <div className="table-head">
                    <h2 className="table-head__title">Positions</h2>
                    <button className="btn btn--outline btn--circle" title="How is Paid set?"
                        onClick={() => setShowPaidHelp(true)}>?</button>
                </div>
                {showPaidHelp && (
                    <Modal title="How 'Paid' works" onClose={() => setShowPaidHelp(false)}>
                        <p>
                            <strong>Paid</strong> is each copy's cost basis — what P/L and the
                            S&amp;P comparison measure against.
                        </p>
                        <p>
                            <strong>Auto price (the default).</strong> Each copy's Paid is set to the
                            card's market price, at its condition, on the day you acquired it. Change
                            the acquired date or grade and it recalculates. If no price data goes back
                            that far, Paid is $0 — meaning its full current value counts as gain.
                        </p>
                        <p>
                            <strong>Set it yourself.</strong> Open a position's copies (click the row),
                            uncheck <em>Auto price</em>, and type the real amount. Use this whenever you
                            know what you actually paid — it makes your P/L honest.
                        </p>
                        <p>
                            <strong>Pulled it from a pack?</strong> A fair basis is the pack price
                            (typically $4–6): assign it to the best card you pulled and let the other
                            pulls ride at $0, or split it evenly across the cards you kept — a $5 pack
                            across five keepers is $1 each.
                        </p>
                        <p>
                            <strong>Pulled it from a box?</strong> Divide the box price by its pack
                            count to get a per-pack cost — a $90 booster box of 24 packs is about
                            $3.75 per pack — then apply the same idea: per-pack cost on each notable
                            pull, $0 on the rest.
                        </p>
                    </Modal>
                )}

                <TrackedFilters params={params} actions={ownedParamsSlice.actions}
                    sortGroups={trackedSortGroups} />

                {isLoading ? (
                    <CardLoader />
                ) : data && data.items.length > 0 ? (
                    <>
                        <div className="screener-wrap">
                            <table className="screener">
                                <thead>
                                    <tr>
                                        <th aria-label="Card image" />
                                        <SortTh label="Card" k="name" ascFirst
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="Condition" k="condition" className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="Qty" k="qty" className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="Paid" k="paid" className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="Mkt value" k="value" className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="P/L" k="pl" className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label={`${hasYear ? '1Y' : '6M'} fcst`}
                                            k={hasYear ? 'chgPct12' : 'chgPct6'} className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <SortTh label="Trend" k={`histPct${params.trend ?? '1m'}`} className="screener__mid"
                                            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
                                        <th aria-label="Actions" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map(card => (
                                        <PositionRow card={card} hasYear={hasYear} key={
                                            `${card.id}:${card.ownedGrade ?? ''}:` +
                                            (card.ownedCopies?.length === 1 ? card.ownedCopies[0].id : 'stack')
                                        } />
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <AppPagination
                            metadata={data.pagination}
                            onPageChange={(page: number) => dispatch(setPageNumber(page))}
                        />
                    </>
                ) : (
                    <p className="est-note">
                        No cards in your portfolio yet — browse the <Link to="/catalog">catalog</Link> and
                        tap "＋ Add" on any card.
                    </p>
                )}
            </div>
        </>
    );
}
