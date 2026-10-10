import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useRemoveOwnedCopyMutation,
} from "./watchlistApi";
import { tierLabel } from "./grades";

type Props = {
    game: string;
    productId: number;
    grade?: string;     // condition the copy is added at ('' = ungraded)
};

// Catalog "quick add" strip (2026-10-10, user request): one click adds ONE
// copy — a pack pull at the selected condition — with no reveal, no spinning
// card and no quantity prompt. The owned count updates in place, − undoes
// the newest copy, and the watchlist star rides along. Signed-in only.
export default function QuickAdd({ game, productId, grade }: Props) {
    const { data: watchlist } = useFetchWatchlistQuery();
    const [add, { isLoading: adding }] = useAddToWatchlistMutation();
    const [removeKind, { isLoading: unwatching }] = useRemoveFromWatchlistMutation();
    const [removeCopy, { isLoading: removing }] = useRemoveOwnedCopyMutation();

    const g = grade ?? '';
    const copies = (watchlist ?? []).filter(w =>
        w.game === game && w.productId === productId && w.kind === 'owned'
        && (w.grade ?? '') === g && (w.printing ?? '') === '');
    const owned = copies.length;
    const newest = copies.reduce<number | null>((m, w) => (m === null || w.id > m ? w.id : m), null);
    const wishlisted = !!watchlist?.some(w =>
        w.game === game && w.productId === productId && w.kind === 'wishlist');
    const busy = adding || removing;

    return (
        <div className="quick-add" onClick={e => e.stopPropagation()}>
            <button className="btn btn--outline quick-add__minus" disabled={busy || owned === 0}
                onClick={() => newest !== null && removeCopy({ id: newest })}
                title={owned ? `Remove one copy (${tierLabel(g)})` : 'No copies to remove'}
                aria-label="Remove one copy">
                −
            </button>
            <span className={`quick-add__count${owned ? ' quick-add__count--some' : ''}`}
                title={`Copies you own · ${tierLabel(g)}`}>
                {owned}
            </span>
            <button className="btn btn--outline quick-add__plus" disabled={busy}
                onClick={() => add({ game, productId, kind: 'owned', grade: g })}
                title={`Add one copy to your portfolio as a pack pull · ${tierLabel(g)}`}>
                {adding ? '…' : '＋ Add'}
            </button>
            <button className={`btn btn--outline quick-add__star${wishlisted ? ' btn--active' : ''}`}
                disabled={unwatching || adding}
                onClick={() => wishlisted
                    ? removeKind({ game, productId, kind: 'wishlist' })
                    : add({ game, productId, kind: 'wishlist' })}
                title={wishlisted ? 'Remove from watchlist' : 'Add to watchlist'}
                aria-label={wishlisted ? 'Remove from watchlist' : 'Add to watchlist'}>
                {wishlisted ? '★' : '☆'}
            </button>
        </div>
    );
}
