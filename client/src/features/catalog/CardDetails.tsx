import { useParams } from "react-router-dom"
import { useFetchCardDetailsQuery } from "./catalogApi";
import { currencyFormat } from "../../lib/util";

export default function CardDetails() {
    const { game, id } = useParams();
    const { data: card, isLoading } = useFetchCardDetailsQuery(
        { game: game ?? 'onepiece', id: id ? +id : 0 }
    );

    if (isLoading || !card) return <div>Is loading...</div>

    const cardDetails = [
        { label: 'Name', value: card.name },
        { label: 'Game', value: card.game },
        { label: 'Set', value: card.setName },
        { label: 'Rarity', value: card.rarity },
        { label: 'Card number', value: card.cardNumber },
        { label: 'Type', value: card.cardType },
        ...Object.entries(card.attributes).map(([label, value]) => ({ label, value })),
        { label: 'Description', value: card.description },
    ].filter(detail => detail.value);

    return (
        <div className="detail">
            <div>
                <img
                    src={card.pictureUrl}
                    alt={card.name}
                    onError={(e) => {
                        const img = e.currentTarget;
                        if (card.imageUrl && img.src !== card.imageUrl) img.src = card.imageUrl;
                        else img.onerror = null;
                    }}
                />
            </div>
            <div>
                <h3>{card.name}</h3>
                <hr className="divider" />
                <div className="card__price" style={{ fontSize: '2rem' }}>{currencyFormat(card.price)}</div>
                <table className="detail-table">
                    <tbody>
                        {cardDetails.map((detail, index) => (
                            <tr key={index}>
                                <td>{detail.label}</td>
                                <td>{detail.value}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
