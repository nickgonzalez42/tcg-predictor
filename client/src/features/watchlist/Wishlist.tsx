import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import {
    useFetchTrackedCardsQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetWishlistAlertMutation,
} from "./watchlistApi";
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


// Alert chip: "🔔 ≤ $280" (highlighted when the price is at/near the target),
// "+ set alert" when unset. Click to edit; Clear removes it.
function AlertChip({ card }: { card: Card }) {
    const [setAlert, { isLoading }] = useSetWishlistAlertMutation();
    const [editing, setEditing] = useState(false);
    const [value, setValue] = useState('');

    const game = gameKey(card.game);
    const target = card.alertTargetPrice;
    const near = target != null && card.price != null && card.price <= target * 1.05;
    const hit = target != null && card.price != null && card.price <= target;

    const submit = async () => {
        const parsed = Number(value);
        if (!value.trim() || !isFinite(parsed) || parsed <= 0) return;
        try {
            await setAlert({ game, productId: card.id, target: parsed }).unwrap();
            setEditing(false);
            setValue('');
        } catch { /* keep the editor open on failure */ }
    };

    if (editing) {
        return (
            <span className="own-qty">
                <input className="input own-qty__input" type="number" min="0" step="0.01" inputMode="decimal"
                    placeholder="$" value={value} autoFocus
                    onChange={e => setValue(e.target.value)}
                    onKeyDown={e => {
                        if (e.key === 'Enter') submit();
                        if (e.key === 'Escape') setEditing(false);
                    }} />
                <button className="btn btn--outline" disabled={isLoading} onClick={submit}>Set</button>
                {target != null && (
                    <button className="btn btn--outline" disabled={isLoading}
                        onClick={async () => {
                            try {
                                await setAlert({ game, productId: card.id, target: null }).unwrap();
                                setEditing(false);
                            } catch { /* leave editor open */ }
                        }}>
                        Clear
                    </button>
                )}
                <button className="btn btn--outline" onClick={() => setEditing(false)}>✕</button>
            </span>
        );
    }

    return target != null ? (
        <button
            className={`alert-chip${hit ? ' alert-chip--hit' : near ? ' alert-chip--near' : ''}`}
            onClick={() => { setValue(String(target)); setEditing(true); }}
            title={hit ? 'Target reached' : near ? 'Close to target' : 'Edit alert'}
        >
            🔔 ≤ {currencyFormat(target)}{hit ? ' · hit!' : near ? ' · close!' : ''}
        </button>
    ) : (
        <button className="alert-chip alert-chip--unset" onClick={() => setEditing(true)}>
            + set alert
        </button>
    );
}

// One watched card, laid out like a catalog screener row: row click opens the
// card; the alert + action cells are interactive and don't bubble.
function WishRow({ card, ownGrade, fcstLabel }: { card: Card; ownGrade: string; fcstLabel: string }) {
    const navigate = useNavigate();
    const [remove, { isLoading: removing }] = useRemoveFromWatchlistMutation();
    const [addOwned, { isLoading: adding }] = useAddToWatchlistMutation();
    const [owned, setOwned] = useState(false);

    const game = gameKey(card.game);
    const detailPath = `/catalog/${game}/${card.id}`;
    const sincePct = card.watchedAtPrice && card.price != null
        ? (card.price / card.watchedAtPrice - 1) * 100
        : null;
    // Forecast % over the horizon the trend buttons snapped to (as on catalog).
    const fcstPct = card.fcstTo != null && card.price
        ? (card.fcstTo / card.price - 1) * 100 : undefined;

    const ownIt = async () => {
        try {
            await addOwned({ game, productId: card.id, kind: 'owned', grade: ownGrade }).unwrap();
            setOwned(true);
        } catch { /* leave the button enabled to retry */ }
    };

    return (
        <tr className="screener__row" onClick={() => navigate(detailPath)}>
            <CardThumbCell card={card} />
            <td className="screener__name">{card.name}</td>
            <td><span className="mono">{[card.setName, card.rarity].filter(Boolean).join(' · ')}</span></td>
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
            <td className="screener__mid" onClick={e => e.stopPropagation()}><AlertChip card={card} /></td>
            <td className="screener__actions" onClick={e => e.stopPropagation()}>
                <button className={`btn btn--outline${owned ? ' btn--active' : ''}`} disabled={adding || owned}
                    onClick={ownIt} title={`Add a copy to your portfolio (${tierLabel(ownGrade)})`}>
                    {owned ? '✓ Owned' : '＋ Own it'}
                </button>
                <button className="star-btn" disabled={removing} title="Remove from watchlist"
                    onClick={() => remove({ game, productId: card.id, kind: 'wishlist' })}>
                    ★
                </button>
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
    const fcstLabel = TREND_FCST[period] ?? '12M';

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
                    {(['1w', '1m', '6m', '1y'] as const).map(t => (
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
                    No cards on your watchlist yet — browse the <Link to="/catalog">catalog</Link> and
                    tap "☆ Watchlist" on any card.
                </p>
            )}
        </div>
    );
}
