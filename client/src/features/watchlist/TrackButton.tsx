import { useUserInfoQuery } from "../account/accountApi";
import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
} from "./watchlistApi";

type Props = { game: string; productId: number; compact?: boolean };

export default function TrackButton({ game, productId, compact }: Props) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add] = useAddToWatchlistMutation();
    const [remove] = useRemoveFromWatchlistMutation();

    if (!user) return null; // tracking is a signed-in feature

    const tracked = !!watchlist?.some(w => w.game === game && w.productId === productId);
    const toggle = () => (tracked ? remove({ game, productId }) : add({ game, productId }));

    return (
        <button
            className={`btn btn--outline${tracked ? ' btn--active' : ''}`}
            onClick={toggle}
            title={tracked ? 'Remove from your list' : 'Add to your list'}
        >
            {tracked ? '★' : '☆'}{compact ? '' : tracked ? ' Tracked' : ' Track'}
        </button>
    );
}
