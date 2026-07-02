import type { Card } from "../../app/models/card"
import { Link } from "react-router-dom"
import { currencyFormat, gameKey } from "../../lib/util"
import TrackButton from "../watchlist/TrackButton"

type Props = {
    card: Card
}

export default function CardItem({ card }: Props) {
    return (
        <div className="card">
            <img
                className="card__media"
                style={{ width: '100%', objectFit: 'contain' }}
                src={card.pictureUrl}
                alt={card.name}
                onError={(e) => {
                    const img = e.currentTarget;
                    if (card.imageUrl && img.src !== card.imageUrl) img.src = card.imageUrl;
                    else img.onerror = null;
                }}
            />
            <div className="card__body">
                <div className="card__title">{card.name}</div>
                <div className="card__price">{card.price != null ? currencyFormat(card.price) : '—'}</div>
            </div>
            <div className="card__actions">
                <Link className="btn btn--outline" to={`/catalog/${gameKey(card.game)}/${card.id}`}>View</Link>
                <TrackButton game={gameKey(card.game)} productId={card.id} compact />
            </div>
        </div>
    )
}
