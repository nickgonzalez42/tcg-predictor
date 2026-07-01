import { useParams } from "react-router-dom"
import { useFetchCardDetailsQuery, useFetchCardForecastQuery } from "./catalogApi";
import { currencyFormat, pctVsMarket } from "../../lib/util";
import PriceHistoryChart from "./PriceHistoryChart";

const TARGET_LABEL: Record<string, string> = { ungraded: 'Ungraded', psa10: 'PSA 10' };
const HORIZON_LABEL: Record<string, string> = { '6m': '6 months', '12m': '12 months' };

export default function CardDetails() {
    const { game, id } = useParams();
    const { data: card, isLoading } = useFetchCardDetailsQuery(
        { game: game ?? 'onepiece', id: id ? +id : 0 }
    );
    const { data: forecastData } = useFetchCardForecastQuery(
        { game: game ?? 'onepiece', id: id ? +id : 0 }
    );

    if (isLoading || !card) return <div>Is loading...</div>

    const forecasts = [...(forecastData?.forecasts ?? [])].sort((a, b) =>
        a.target.localeCompare(b.target) || a.horizon.localeCompare(b.horizon));

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
        <>
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
        <section className="chart-section">
            <h4 className="graded-title">Price history</h4>
            <PriceHistoryChart game={game ?? 'onepiece'} id={id ? +id : 0} />
        </section>
        {forecasts.length > 0 && (
            <section className="chart-section">
                <h4 className="graded-title">
                    Price forecast <span className="est-note">· model</span>
                </h4>
                <table className="forecast-table">
                    <thead>
                        <tr><th>Tier</th><th>Horizon</th><th>Forecast</th><th>Range</th><th>Change</th></tr>
                    </thead>
                    <tbody>
                        {forecasts.map((f, i) => {
                            const chg = f.basePrice ? (f.forecastPrice / f.basePrice - 1) * 100 : 0;
                            return (
                                <tr key={i}>
                                    <td>{TARGET_LABEL[f.target] ?? f.target}</td>
                                    <td>{HORIZON_LABEL[f.horizon] ?? f.horizon}</td>
                                    <td><strong>{currencyFormat(f.forecastPrice)}</strong></td>
                                    <td className="est-note">{currencyFormat(f.low)}–{currencyFormat(f.high)}</td>
                                    <td>
                                        <span className={`valuation ${chg >= 0 ? 'valuation--up' : 'valuation--down'}`}>
                                            {chg >= 0 ? '+' : ''}{chg.toFixed(0)}%
                                        </span>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                <div className="est-note" style={{ marginTop: '0.5rem' }}>
                    Directional model; range is a confidence band. Not investment advice.
                </div>
            </section>
        )}
        </>
    )
}
