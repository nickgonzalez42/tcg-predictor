import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { createChart, AreaSeries, ColorType } from "lightweight-charts";
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
import { trackedSortOptions } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import GameToggle from "../../app/shared/components/GameToggle";
import CardThumbCell from "../../app/shared/components/CardThumbCell";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import type { Card } from "../../app/models/card";


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

// Green portfolio value-over-time chart (monthly points from the summary).
function ValueChart({ summary }: { summary: PortfolioSummary }) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [range, setRange] = useState('all');

    useEffect(() => {
        const el = containerRef.current;
        const all = summary.series;
        if (!el || !all?.length) return;

        const months = RANGES.find(r => r.key === range)?.months;
        const points = months ? all.slice(-(months + 1)) : all;
        if (points.length < 2) return;

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
        chart.timeScale().fitContent();

        return () => chart.remove();
    }, [summary, range]);

    if (!summary.series?.length) return null;

    return (
        <div className="panel detail-panel">
            <div className="chart-tabs">
                <span className="mono detail-panel__title">Value over time</span>
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
            <div ref={containerRef} style={{ width: '100%' }} />
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
function PositionRow({ card }: { card: Card }) {
    const [expanded, setExpanded] = useState(false);
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

    return (
        <>
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
                <td className="screener__num"><ChangePill value={card.fcst12Pct} title="12 month model forecast" /></td>
                <td><Sparkline points={card.sparkline} /></td>
                <td className="screener__actions" onClick={e => e.stopPropagation()}>
                    <button className="btn btn--outline" disabled={removing || qty === 0} onClick={removeOne}
                        title="Remove one copy">−</button>
                    <button className="btn btn--outline" disabled={adding} onClick={addOne}
                        title="Add one copy">＋</button>
                    <button className={`btn btn--outline${expanded ? ' btn--active' : ''}`}
                        onClick={() => setExpanded(v => !v)} title="Edit copies (paid price, date, note)">✎</button>
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
                            {copies.map(copy => <OwnedCopyRow key={copy.id} copy={copy} />)}
                        </div>
                    </td>
                </tr>
            )}
        </>
    );
}

export default function Portfolio() {
    const { setGame, setOrderBy, setSearchTerm, setPageNumber } = ownedParamsSlice.actions;
    const params = useAppSelector(state => state.ownedParams);
    const dispatch = useAppDispatch();

    const { data, isLoading } = useFetchTrackedCardsQuery({ kind: 'owned', ...params });
    const { data: summary } = useFetchPortfolioSummaryQuery();

    const { term, onChange: search } = useDebouncedSearch(
        params.searchTerm ?? '', v => dispatch(setSearchTerm(v)));

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
                    <input className="input table-head__search" type="search" placeholder="Search…"
                        value={term} onChange={e => search(e.target.value)} />
                    <select className="input table-head__sort" value={params.orderBy}
                        onChange={e => dispatch(setOrderBy(e.target.value))} title="Sort positions">
                        {trackedSortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <GameToggle game={params.game} onChange={g => dispatch(setGame(g))} />
                </div>

                {isLoading ? (
                    <div>Loading...</div>
                ) : data && data.items.length > 0 ? (
                    <>
                        <div className="screener-wrap">
                            <table className="screener">
                                <thead>
                                    <tr>
                                        <th aria-label="Card image" />
                                        <th>Card</th>
                                        <th>Condition</th>
                                        <th className="screener__num">Qty</th>
                                        <th className="screener__num">Paid</th>
                                        <th className="screener__num">Mkt value</th>
                                        <th className="screener__num">P/L</th>
                                        <th className="screener__num">12m fcst</th>
                                        <th>Trend</th>
                                        <th aria-label="Actions" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map(card => (
                                        <PositionRow card={card} key={
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
