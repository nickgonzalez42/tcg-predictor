import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import {
    useFetchTrackedCardsQuery,
    useRemoveFromWatchlistMutation,
    useFetchAlertsQuery,
} from "./watchlistApi";
import OwnItModal from "./OwnItModal";
import AlertModal from "./AlertModal";
import { wishlistParamsSlice } from "./trackedParamsSlice";
import { tierLabel } from "./grades";
import { trackedSortGroups } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import CardThumbCell from "../../app/shared/components/CardThumbCell";
import TrackedFilters from "./TrackedFilters";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import CardLoader from "../../app/shared/components/CardLoader";
import { TREND_FCST } from "../catalog/CardTable";
import type { Card } from "../../app/models/card";
import { usePageMeta } from "../../lib/usePageMeta";


// Alert chip: "🔔 2" (highlighted when any alert has hit) or "+ set alert".
// Opens the alert manager modal — several alerts per card, on the actual
// price, forecast prices, or forecast % growth, per condition and timeframe.
function AlertChip({ card, ownGrade }: { card: Card; ownGrade: string }) {
    const { data: alerts } = useFetchAlertsQuery();   // one fetch, shared by every row
    const [open, setOpen] = useState(false);

    const game = gameKey(card.game);
    const mine = (alerts ?? []).filter(a => a.game === game && a.productId === card.id);
    const anyHit = mine.some(a => a.hit);

    return (
        <>
            {mine.length > 0 ? (
                <button className={`alert-chip${anyHit ? ' alert-chip--hit' : ''}`}
                    onClick={() => setOpen(true)}
                    title={anyHit ? 'An alert has hit — manage alerts' : 'Manage alerts'}>
                    🔔 {mine.length}{anyHit ? ' · hit!' : ''}
                </button>
            ) : (
                <button className="alert-chip alert-chip--unset" onClick={() => setOpen(true)}>
                    + set alert
                </button>
            )}
            {open && (
                <AlertModal card={card} game={game} defaultGrade={ownGrade}
                    onClose={() => setOpen(false)} />
            )}
        </>
    );
}

// One watched card, laid out like a catalog screener row: row click opens the
// card; the alert + action cells are interactive and don't bubble.
function WishRow({ card, ownGrade, fcstLabel }: { card: Card; ownGrade: string; fcstLabel: string }) {
    const navigate = useNavigate();
    const [remove, { isLoading: removing }] = useRemoveFromWatchlistMutation();
    // "Own it" opens a modal for the copy's details; on success the modal adds
    // it to the portfolio and removes this row from the watchlist.
    const [showOwn, setShowOwn] = useState(false);

    const game = gameKey(card.game);
    const detailPath = `/catalog/${game}/${card.id}`;
    const sincePct = card.watchedAtPrice && card.price != null
        ? (card.price / card.watchedAtPrice - 1) * 100
        : null;
    // Forecast % over the horizon the trend buttons snapped to (as on catalog).
    const fcstPct = card.fcstTo != null && card.price
        ? (card.fcstTo / card.price - 1) * 100 : undefined;

    return (
        <tr className="screener__row" onClick={() => navigate(detailPath)}>
            <CardThumbCell card={card} />
            <td className="screener__name">{card.name}</td>
            <td><span className="mono">{[card.setName, card.rarity].filter(Boolean).join(' · ')}</span></td>
            <td className="screener__mid">
                <span className="mono">{card.watchedSince ? shortDate(card.watchedSince) : '—'}</span>
            </td>
            <td className="screener__num">{card.watchedAtPrice != null ? currencyFormat(card.watchedAtPrice) : '—'}</td>
            <td className="screener__num screener__price">
                {card.price != null ? currencyFormat(card.price) : '—'}
                {card.priceAsOf && <div className="mono price-asof">{shortDate(card.priceAsOf)}</div>}
            </td>
            <td className="screener__mid">
                {sincePct != null ? <ChangePill value={sincePct} title="Change since added" /> : <span className="mono">—</span>}
            </td>
            <td className="screener__mid"><ChangePill value={fcstPct} title={`${fcstLabel} model forecast`} /></td>
            <td className="screener__mid"><Sparkline points={card.sparkline} /></td>
            <td className="screener__mid" onClick={e => e.stopPropagation()}><AlertChip card={card} ownGrade={ownGrade} /></td>
            <td className="screener__actions" onClick={e => e.stopPropagation()}>
                <button className="btn btn--outline"
                    onClick={() => setShowOwn(true)} title="Add to your portfolio">
                    ＋ Own it
                </button>
                <button className="star-btn" disabled={removing} title="Remove from watchlist"
                    onClick={() => remove({ game, productId: card.id, kind: 'wishlist' })}>
                    ★
                </button>
                {showOwn && (
                    <OwnItModal card={card} game={game} defaultGrade={ownGrade}
                        onClose={() => setShowOwn(false)} />
                )}
            </td>
        </tr>
    );
}

export default function Wishlist() {
    usePageMeta("Watchlist");
    const { setPageNumber, setTrend } = wishlistParamsSlice.actions;
    const params = useAppSelector(state => state.wishlistParams);
    const dispatch = useAppDispatch();

    const { data, isLoading } = useFetchTrackedCardsQuery({ kind: 'wishlist', ...params });
    // The selected price tier IS the condition vocabulary here: '' = Ungraded.
    // Owning a card at '' creates an unspecified-condition copy (priced ungraded).
    const ownGrade = params.grade ?? '';
    const totalCount = data?.pagination?.totalCount;
    // Same window mechanics as catalog row view: the tabs drive the sparkline,
    // the Past column and the forecast column's mapped horizon.
    const period = (params.trend ?? '1m').toLowerCase();
    const fcstLabel = TREND_FCST[period] ?? '1Y';

    return (
        <div className="full-span">
            <div className="table-head">
                <h2 className="table-head__title">
                    Watchlist{totalCount != null && (
                        <span className="est-note"> · {totalCount} card{totalCount === 1 ? '' : 's'} watched</span>
                    )}
                </h2>
                <div className="range-tabs" role="group" aria-label="Trend period"
                    title="Window for the trend line and price movement (price data updates monthly)">
                    {(['1m', '6m', '1y'] as const).map(t => (
                        <button key={t}
                            className={`btn btn--outline range-tab${period === t ? ' btn--active' : ''}`}
                            onClick={() => dispatch(setTrend(t))}
                            aria-pressed={period === t}
                        >
                            {t.toUpperCase()}
                        </button>
                    ))}
                </div>
            </div>

            <TrackedFilters params={params} actions={wishlistParamsSlice.actions}
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
                                    <th>Card</th>
                                    <th>Set / Rarity</th>
                                    <th className="screener__mid">Watching since</th>
                                    <th className="screener__num">Watching at</th>
                                    <th className="screener__num">
                                        {tierLabel(ownGrade)} price
                                    </th>
                                    <th className="screener__mid">Since added</th>
                                    <th className="screener__mid"
                                        title="Model forecast over the horizon matching the selected window">
                                        {fcstLabel} fcst
                                    </th>
                                    <th className="screener__mid" title="Actual price history over the selected window">
                                        Past {period.toUpperCase()}
                                    </th>
                                    <th className="screener__mid">Alert</th>
                                    <th aria-label="Actions" />
                                </tr>
                            </thead>
                            <tbody>
                                {data.items.map(card => (
                                    <WishRow card={card} ownGrade={ownGrade} fcstLabel={fcstLabel} key={card.id} />
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
                    No cards on your watchlist yet. Browse the <Link to="/catalog">catalog</Link> and
                    tap "☆ Watchlist" on any card.
                </p>
            )}
        </div>
    );
}
