import { useState } from "react";
import { useUserInfoQuery } from "../account/accountApi";
import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetOwnedQuantityMutation,
} from "./watchlistApi";
import { tierLabel } from "./grades";
import SourceToggle from "./SourceToggle";

type Props = {
    game: string;
    productId: number;
    compact?: boolean;
    ownGrade?: string;      // price tier the quick-add applies to ('' = ungraded -> unspecified copy)
};

export default function TrackButton({ game, productId, compact, ownGrade }: Props) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add, { isLoading: addPending }] = useAddToWatchlistMutation();
    const [remove, { isLoading: removePending }] = useRemoveFromWatchlistMutation();
    const wishlistPending = addPending || removePending;
    // While the "how many to add" input is open, the watchlist button is hidden
    // (it comes back on Cancel), so the quantity row isn't crowded.
    const [adding, setAdding] = useState(false);

    if (!user) return null; // tracking is a signed-in feature

    const wishlisted = !!watchlist?.some(
        w => w.game === game && w.productId === productId && w.kind === 'wishlist');

    const wishlistButton = (
        <button
            className={`btn btn--outline${wishlisted ? ' btn--active' : ''}`}
            disabled={wishlistPending}
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
        <div className="track-buttons" style={{ display: 'inline-flex', gap: 'var(--space-5)', alignItems: 'stretch' }}>
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
    // Pack pull by default; a paid copy auto-prices to today's market unless
    // a manual price is typed.
    const [source, setSource] = useState<'pack' | 'paid'>('pack');
    const [autoPrice, setAutoPrice] = useState(true);
    const [price, setPrice] = useState('');

    const parsed = Number(value);
    const priceNum = Number(price);
    const priceOk = source !== 'paid' || autoPrice
        || (price.trim() !== '' && isFinite(priceNum) && priceNum >= 0);
    const valid = value.trim() !== '' && Number.isInteger(parsed) && parsed >= 1 && parsed <= 999
        && priceOk;

    const close = () => {
        setOpen(false); setValue('1');
        setSource('pack'); setAutoPrice(true); setPrice('');
        onOpenChange?.(false);
    };
    const submit = async () => {
        if (!valid || isLoading) return;
        try {
            await setQty({
                game, productId, grade,
                quantity: Math.min(owned + parsed, 999),
                source,
                purchasePrice: source === 'paid' && !autoPrice ? priceNum : undefined,
            }).unwrap();
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
            <SourceToggle value={source} onChange={v => {
                setSource(v);
                if (v === 'pack') { setAutoPrice(true); setPrice(''); }
            }} disabled={isLoading} />
            {source === 'paid' && (
                <label className="auto-price-check" style={{ margin: 0 }}
                    title="Use today's market price as the price paid">
                    <input type="checkbox" checked={autoPrice}
                        onChange={e => setAutoPrice(e.target.checked)} />
                    auto&nbsp;$
                </label>
            )}
            {source === 'paid' && !autoPrice && (
                <input
                    className="input own-qty__input"
                    type="number" min="0" step="any" inputMode="decimal"
                    placeholder="$" aria-label="Price paid per copy"
                    value={price} disabled={isLoading}
                    onChange={e => setPrice(e.target.value)}
                />
            )}
            <input
                className="input own-qty__input"
                type="number" min="1" max="999" step="1" inputMode="numeric"
                value={value} autoFocus disabled={isLoading}
                onChange={e => setValue(e.target.value)}
                onKeyDown={e => {
                    if (e.key === 'Enter') submit();
                    if (e.key === 'Escape') close();
                }}
            />
            <button className="btn btn--outline" disabled={!valid || isLoading} onClick={submit}
                title={`Add to portfolio · ${tierLabel(grade)}`}>
                {isLoading ? 'Adding…' : 'Add'}
            </button>
            <button className="btn btn--outline" disabled={isLoading} onClick={close}>Cancel</button>
        </span>
    );
}
