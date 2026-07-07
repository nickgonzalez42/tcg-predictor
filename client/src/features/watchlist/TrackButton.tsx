import { useState } from "react";
import { useUserInfoQuery } from "../account/accountApi";
import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetOwnedQuantityMutation,
} from "./watchlistApi";
import { OWNED_CONDITIONS, conditionLabel } from "./grades";

type Props = {
    game: string;
    productId: number;
    compact?: boolean;
    ownGrade?: string;      // condition the owned quantity applies to (copy vocab); defaults to Near Mint
    chooseGrade?: boolean;  // detail page: condition picker + add-a-copy button instead of the quantity field
};

export default function TrackButton({ game, productId, compact, ownGrade, chooseGrade }: Props) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add] = useAddToWatchlistMutation();
    const [remove] = useRemoveFromWatchlistMutation();
    const [pickGrade, setPickGrade] = useState('nm');

    if (!user) return null; // tracking is a signed-in feature

    const wishlisted = !!watchlist?.some(
        w => w.game === game && w.productId === productId && w.kind === 'wishlist');

    const wishlistButton = (
        <button
            className={`btn btn--outline${wishlisted ? ' btn--active' : ''}`}
            onClick={() => wishlisted
                ? remove({ game, productId, kind: 'wishlist' })
                : add({ game, productId, kind: 'wishlist' })}
            title={wishlisted ? 'Remove from wishlist' : 'Add to wishlist'}
        >
            {wishlisted ? '★' : '☆'}{compact ? '' : ` ${wishlisted ? 'Wishlisted' : 'Wishlist'}`}
        </button>
    );

    // Detail page: pick a condition, add one copy per click.
    if (chooseGrade) {
        const ownedCount = watchlist?.filter(
            w => w.game === game && w.productId === productId && w.kind === 'owned').length ?? 0;
        return (
            <div className="track-buttons" style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
                <select className="input" style={{ width: 'auto' }} value={pickGrade}
                    onChange={e => setPickGrade(e.target.value)} title="Condition to add">
                    {OWNED_CONDITIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <button
                    className={`btn btn--outline${ownedCount > 0 ? ' btn--active' : ''}`}
                    onClick={() => add({ game, productId, kind: 'owned', grade: pickGrade })}
                    title={ownedCount > 0 ? 'Add another copy (manage copies on the Portfolio page)' : 'Add a copy to your portfolio'}
                >
                    {ownedCount > 0 ? '✓' : '＋'} {ownedCount > 0 ? `In portfolio (${ownedCount})` : 'Add to portfolio'}
                </button>
                {wishlistButton}
            </div>
        );
    }

    // Catalog: an Add button that opens a "how many to add" input. Deliberately
    // shows no owned count — the portfolio is managed on the Portfolio page.
    const grade = ownGrade || 'nm';
    const ownedAtGrade = watchlist?.filter(
        w => w.game === game && w.productId === productId
            && w.kind === 'owned' && (w.grade ?? '') === grade).length ?? 0;

    return (
        <div className="track-buttons" style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <AddToCollection game={game} productId={productId} grade={grade} owned={ownedAtGrade} />
            {wishlistButton}
        </div>
    );
}

// "＋ Add" → number input + "Add to portfolio" / "Cancel". Adds N copies at the
// given condition (the server endpoint sets totals, so we send owned + N).
function AddToCollection({ game, productId, grade, owned }: {
    game: string; productId: number; grade: string; owned: number;
}) {
    const [setQty, { isLoading }] = useSetOwnedQuantityMutation();
    const [open, setOpen] = useState(false);
    const [value, setValue] = useState('1');

    const parsed = Number(value);
    const valid = value.trim() !== '' && Number.isInteger(parsed) && parsed >= 1 && parsed <= 999;

    const close = () => { setOpen(false); setValue('1'); };
    const submit = async () => {
        if (!valid || isLoading) return;
        try {
            await setQty({ game, productId, grade, quantity: Math.min(owned + parsed, 999) }).unwrap();
            close();
        } catch {
            // add failed — keep the input open so the user can retry
        }
    };

    if (!open) {
        return (
            <button className="btn btn--outline" onClick={() => setOpen(true)}
                title={`Add copies to your portfolio (${conditionLabel(grade)})`}>
                ＋ Add
            </button>
        );
    }

    return (
        <span className="own-qty" title={`Copies to add · ${conditionLabel(grade)}`}>
            <input
                className="input own-qty__input"
                type="number" min="1" max="999" step="1" inputMode="numeric"
                value={value} autoFocus
                onChange={e => setValue(e.target.value)}
                onKeyDown={e => {
                    if (e.key === 'Enter') submit();
                    if (e.key === 'Escape') close();
                }}
            />
            <button className="btn btn--outline" disabled={!valid || isLoading} onClick={submit}>
                Add to portfolio
            </button>
            <button className="btn btn--outline" onClick={close}>Cancel</button>
        </span>
    );
}
