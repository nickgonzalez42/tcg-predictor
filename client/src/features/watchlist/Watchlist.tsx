import { useFetchWatchlistQuery } from "./watchlistApi";
import { useFetchCardDetailsQuery } from "../catalog/catalogApi";
import CardItem from "../catalog/CardItem";

function WatchlistCard({ game, id }: { game: string; id: number }) {
    const { data: card } = useFetchCardDetailsQuery({ game, id });
    if (!card) return null;
    return <CardItem card={card} />;
}

export default function Watchlist() {
    const { data: items, isLoading } = useFetchWatchlistQuery();

    if (isLoading) return <div>Loading...</div>;

    return (
        <div>
            <h2>My List</h2>
            {items && items.length > 0 ? (
                <div className="product-grid">
                    {items.map(i => (
                        <WatchlistCard key={`${i.game}-${i.productId}`} game={i.game} id={i.productId} />
                    ))}
                </div>
            ) : (
                <p className="est-note">No tracked cards yet — browse the catalog and tap ☆ Track on any card.</p>
            )}
        </div>
    );
}
