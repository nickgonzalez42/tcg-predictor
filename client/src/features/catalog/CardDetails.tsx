import { useParams } from "react-router-dom"
import { useFetchCardDetailsQuery } from "./catalogApi";
import { currencyFormat, pctVsMarket } from "../../lib/util";

export default function CardDetails() {
    const { game, id } = useParams();
    const { data: card, isLoading } = useFetchCardDetailsQuery(
        { game: game ?? 'onepiece', id: id ? +id : 0 }
    );

    if (isLoading || !card) return <div>Is loading...</div>

    const pct = pctVsMarket(card.price, card.predictedPrice);

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

    const g = card.gradedPrices;
    const gradeRows = g ? [
        { label: 'Ungraded', value: g.ungraded },
        { label: 'Grade 7', value: g.grade7 },
        { label: 'Grade 8', value: g.grade8 },
        { label: 'Grade 9', value: g.grade9 },
        { label: 'Grade 9.5', value: g.grade95 },
        { label: 'PSA 10', value: g.psa10 },
        { label: 'BGS 10', value: g.bgs10 },
        { label: 'CGC 10', value: g.cgc10 },
        { label: 'SGC 10', value: g.sgc10 },
    ].filter(r => r.value != null) : [];

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
                {card.price != null && (
                    <div className="card__price" style={{ fontSize: '2rem' }}>
                        {currencyFormat(card.price)} <span className="price-caption">market</span>
                    </div>
                )}
                {card.predictedPrice != null && (
                    <div className="estimate">
                        Model estimate: <strong>{currencyFormat(card.predictedPrice)}</strong>
                        {pct != null && (
                            <span className={`valuation ${pct >= 0 ? 'valuation--up' : 'valuation--down'}`}>
                                {pct >= 0 ? '+' : ''}{pct.toFixed(0)}% vs market
                            </span>
                        )}
                        {card.usedImage && <span className="est-note"> · uses card art</span>}
                    </div>
                )}
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
                {gradeRows.length > 0 && (
                    <>
                        <h4 className="graded-title">
                            Graded prices <span className="est-note">· PriceCharting</span>
                        </h4>
                        <table className="detail-table">
                            <tbody>
                                {gradeRows.map((r, i) => (
                                    <tr key={i}>
                                        <td>{r.label}</td>
                                        <td>{currencyFormat(r.value)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </>
                )}
            </div>
        </div>
    )
}
