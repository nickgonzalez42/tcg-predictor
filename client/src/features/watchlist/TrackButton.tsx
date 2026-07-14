import { useState } from "react";
import { useUserInfoQuery } from "../account/accountApi";
import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetOwnedQuantityMutation,
} from "./watchlistApi";
import { tierLabel } from "./grades";

type Props = {
    game: string;
    productId: number;
    compact?: boolean;
    ownGrade?: string;      // price tier the quick-add applies to ('' = ungraded -> unspecified copy)
};

export default function TrackButton({ game, productId, compact, ownGrade }: Props) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add] = useAddToWatchlistMutation();
    const [remove] = useRemoveFromWatchlistMutation();
    // While the "how many to add" input is open, the watchlist button is hidden
    // (it comes back on Cancel), so the quantity row isn't crowded.
    const [adding, setAdding] = useState(false);

    if (!user) return null; // tracking is a signed-in feature

    const wishlisted = !!watchlist?.some(
        w => w.game === game && w.productId === productId && w.kind === 'wishlist');

    const wishlistButton = (
        <button
            className={`btn btn--outline${wishlisted ? ' btn--active' : ''}`}
            onClick={() => wishlisted
                ? remove({ game, productId, kind: 'wishlist' })
                : add({ game, productId, kind: 'wishlist' })}
            title={wishlisted ? 'Remove from watchlist' : 'Add to watchlist'}
        >
            {wishlisted ? '★' : '☆'}{compact ? '' : ` ${wishlisted ? 'Watching' : 'Watchlist'}`}
        </button>
    );


    // Catalog: an Add button that opens a "how many to add" input. Deliberately
    // shows no owned count — the portfolio is managed on the Portfolio page.
    const grade = ownGrade ?? '';
    const ownedAtGrade = watchlist?.filter(
        w => w.game === game && w.productId === productId
            && w.kind === 'owned' && (w.grade ?? '') === grade).length ?? 0;

    return (
        <div className="track-buttons" style={{ display: 'inline-flex', gap: 'var(--space-5)', alignItems: 'center' }}>
            <AddToCollection game={game} productId={productId} grade={grade} owned={ownedAtGrade}
                onOpenChange={setAdding} />
            {!adding && wishlistButton}
        </div>
    );
}

// "＋ Add" → number input + "Add to portfolio" / "Cancel". Adds N copies at the
// given condition (the server endpoint sets totals, so we send owned + N).
// onOpenChange lets the parent hide the watchlist button while the input is up.
function AddToCollection({ game, productId, grade, owned, onOpenChange }: {
    game: string; productId: number; grade: string; owned: number;
    onOpenChange?: (open: boolean) => void;
}) {
    const [setQty, { isLoading }] = useSetOwnedQuantityMutation();
    const [open, setOpen] = useState(false);
    const [value, setValue] = useState('1');

    const parsed = Number(value);
    const valid = value.trim() !== '' && Number.isInteger(parsed) && parsed >= 1 && parsed <= 999;

    const close = () => { setOpen(false); setValue('1'); onOpenChange?.(false); };
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
            <button className="btn btn--outline" onClick={() => { setOpen(true); onOpenChange?.(true); }}
                title={`Add copies to your portfolio (${tierLabel(grade)})`}>
                ＋ Add
            </button>
        );
    }

    return (
        <span className="own-qty" title={`Copies to add · ${tierLabel(grade)}`}>
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
            <button className="btn btn--outline" disabled={!valid || isLoading} onClick={submit}
                title={`Add to portfolio · ${tierLabel(grade)}`}>
                Add
            </button>
            <button className="btn btn--outline" onClick={close}>Cancel</button>
        </span>
    );
}
