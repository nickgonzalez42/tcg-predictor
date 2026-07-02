import type { Card } from "../../app/models/card"
import { Link } from "react-router-dom"
import { currencyFormat, gameKey, pctVsMarket } from "../../lib/util"
import TrackButton from "../watchlist/TrackButton"

type Props = {
    card: Card
}

export default function CardItem({ card }: Props) {
    const pct = pctVsMarket(card.price, card.predictedPrice);
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
                {card.price != null ? (
                    <>
                        <div className="card__price">{currencyFormat(card.price)}</div>
                        {card.predictedPrice != null && (
                            <div className="card__est">
                                Model {currencyFormat(card.predictedPrice)}
                                {pct != null && (
                                    <span className={`valuation ${pct >= 0 ? 'valuation--up' : 'valuation--down'}`}>
                                        {pct >= 0 ? '+' : ''}{pct.toFixed(0)}%
                                    </span>
                                )}
                            </div>
                        )}
                    </>
                ) : (
                    <div className="card__price">
                        {currencyFormat(card.predictedPrice)} <span className="est-tag">est</span>
                    </div>
                )}
            </div>
            <div className="card__actions">
                <Link className="btn btn--outline" to={`/catalog/${gameKey(card.game)}/${card.id}`}>View</Link>
                <TrackButton game={gameKey(card.game)} productId={card.id} compact />
            </div>
        </div>
    )
}
