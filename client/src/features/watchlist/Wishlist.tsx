import { useState } from "react";
import { Link } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import {
    useFetchTrackedCardsQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetWishlistAlertMutation,
} from "./watchlistApi";
import { wishlistParamsSlice } from "./trackedParamsSlice";
import { PRICE_TIER_OPTIONS, tierLabel } from "./grades";
import { trackedSortOptions } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import GameToggle from "../../app/shared/components/GameToggle";
import CardThumbCell from "../../app/shared/components/CardThumbCell";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import type { Card } from "../../app/models/card";


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

function WishRow({ card, ownGrade }: { card: Card; ownGrade: string }) {
    const [remove, { isLoading: removing }] = useRemoveFromWatchlistMutation();
    const [addOwned, { isLoading: adding }] = useAddToWatchlistMutation();
    const [owned, setOwned] = useState(false);

    const game = gameKey(card.game);
    const sincePct = card.watchedAtPrice && card.price != null
        ? (card.price / card.watchedAtPrice - 1) * 100
        : null;

    const ownIt = async () => {
        try {
            await addOwned({ game, productId: card.id, kind: 'owned', grade: ownGrade }).unwrap();
            setOwned(true);
        } catch { /* leave the button enabled to retry */ }
    };

    return (
        <tr className="screener__row">
            <td>
                <button className="star-btn" disabled={removing} title="Remove from watchlist"
                    onClick={() => remove({ game, productId: card.id, kind: 'wishlist' })}>
                    ★
                </button>
            </td>
            <CardThumbCell card={card} />
            <td>
                <Link className="screener__name" to={`/catalog/${game}/${card.id}`}>{card.name}</Link>
                <div className="mono">
                    {[card.setName, card.rarity, tierLabel(ownGrade)].filter(Boolean).join(' · ')}
                </div>
            </td>
            <td className="screener__num">{card.watchedAtPrice != null ? currencyFormat(card.watchedAtPrice) : '—'}</td>
            <td className="screener__num screener__price">
                {card.price != null ? currencyFormat(card.price) : '—'}
                {card.priceAsOf && <div className="mono price-asof">{shortDate(card.priceAsOf)}</div>}
            </td>
            <td className="screener__num">
                {sincePct != null ? <ChangePill value={sincePct} title="Change since added" /> : <span className="mono">—</span>}
            </td>
            <td className="screener__num"><ChangePill value={card.fcst12Pct} title="12 month model forecast" /></td>
            <td><Sparkline points={card.sparkline} /></td>
            <td><AlertChip card={card} /></td>
            <td className="screener__actions">
                <button className={`btn btn--outline${owned ? ' btn--active' : ''}`} disabled={adding || owned}
                    onClick={ownIt} title={`Add a copy to your portfolio (${tierLabel(ownGrade)})`}>
                    {owned ? '✓ Owned' : '＋ Own it'}
                </button>
            </td>
        </tr>
    );
}

export default function Wishlist() {
    const { setGame, setOrderBy, setSearchTerm, setGrade, setPageNumber } = wishlistParamsSlice.actions;
    const params = useAppSelector(state => state.wishlistParams);
    const dispatch = useAppDispatch();

    const { data, isLoading } = useFetchTrackedCardsQuery({ kind: 'wishlist', ...params });
    // The selected price tier IS the condition vocabulary here: '' = Ungraded.
    // Owning a card at '' creates an unspecified-condition copy (priced ungraded).
    const ownGrade = params.grade ?? '';
    const totalCount = data?.pagination?.totalCount;

    const { term, onChange: search } = useDebouncedSearch(
        params.searchTerm ?? '', v => dispatch(setSearchTerm(v)));

    return (
        <div className="full-span">
            <div className="table-head">
                <h2 className="table-head__title">
                    Watchlist{totalCount != null && (
                        <span className="est-note"> · {totalCount} card{totalCount === 1 ? '' : 's'} watched</span>
                    )}
                </h2>
                <input className="input table-head__search" type="search" placeholder="Search…"
                    value={term} onChange={e => search(e.target.value)} />
                <GameToggle game={params.game} onChange={g => dispatch(setGame(g))} />
                <select className="input table-head__sort" value={params.grade ?? ''}
                    onChange={e => dispatch(setGrade(e.target.value))} title="Price shown">
                    {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <select className="input table-head__sort" value={params.orderBy}
                    onChange={e => dispatch(setOrderBy(e.target.value))} title="Sort">
                    {trackedSortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
            </div>

            {isLoading ? (
                <div>Loading...</div>
            ) : data && data.items.length > 0 ? (
                <>
                    <div className="screener-wrap">
                        <table className="screener">
                            <thead>
                                <tr>
                                    <th aria-label="Watching" />
                                    <th aria-label="Card image" />
                                    <th>Card</th>
                                    <th className="screener__num">Watching at</th>
                                    <th className="screener__num">Now</th>
                                    <th className="screener__num">Since added</th>
                                    <th className="screener__num">12m fcst</th>
                                    <th>Trend</th>
                                    <th>Alert</th>
                                    <th aria-label="Actions" />
                                </tr>
                            </thead>
                            <tbody>
                                {data.items.map(card => (
                                    <WishRow card={card} ownGrade={ownGrade} key={card.id} />
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
