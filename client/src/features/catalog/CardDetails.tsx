import { useParams } from "react-router-dom"
import { useFetchCardDetailsQuery, useFetchCardForecastQuery } from "./catalogApi";
import { currencyFormat, pctVsMarket } from "../../lib/util";
import PriceHistoryChart from "./PriceHistoryChart";
import TrackButton from "../watchlist/TrackButton";

const TARGETS = ['ungraded', 'grade7', 'grade8', 'grade9', 'grade95', 'psa10', 'bgs10', 'cgc10', 'sgc10'];
const HORIZONS = ['6m', '12m'];
const TARGET_LABEL: Record<string, string> = {
    ungraded: 'Ungraded', grade7: 'Grade 7', grade8: 'Grade 8', grade9: 'Grade 9',
    grade95: 'Grade 9.5', psa10: 'PSA 10', bgs10: 'BGS 10', cgc10: 'CGC 10', sgc10: 'SGC 10',
};
const HORIZON_LABEL: Record<string, string> = { '6m': '6 months', '12m': '12 months' };

// Confidence from how much monthly price history the tier has. The model's
// reliable signal came from cards with years of data; thin history = low trust.
function confidence(months?: number) {
    if (!months || months < 24) return { label: 'Low confidence', cls: 'conf--low' };
    if (months < 48) return { label: 'Medium confidence', cls: 'conf--med' };
    return { label: 'High confidence', cls: 'conf--high' };
}

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
        <div className="detail subgrid">
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
                <TrackButton game={game ?? 'onepiece'} productId={id ? +id : 0} />
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
        <div className="chart-section">
            <section>
                <h4 className="graded-title">Price history</h4>
                <PriceHistoryChart game={game ?? 'onepiece'} id={id ? +id : 0} />
            </section>
            {forecasts.length > 0 && (
                <section>
                    <h4 className="graded-title">
                        Price forecast <span className="est-note">· model</span>
                    </h4>
                    <table className="forecast-table">
                        <thead>
                            <tr>
                                <th>Tier</th>
                                <th>Current</th>
                                {HORIZONS.map(h => <th key={h}>{HORIZON_LABEL[h]}</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {TARGETS.filter(t => forecasts.some(f => f.target === t)).map(t => {
                                const tierForecasts = forecasts.filter(f => f.target === t);
                                const conf = confidence(tierForecasts[0]?.months);
                                return (
                                    <tr key={t}>
                                        <td>
                                            {TARGET_LABEL[t] ?? t}
                                            <div><span className={`conf ${conf.cls}`}>{conf.label}</span></div>
                                        </td>
                                        <td><strong>{currencyFormat(tierForecasts[0]?.basePrice)}</strong></td>
                                        {HORIZONS.map(h => {
                                            const f = tierForecasts.find(x => x.horizon === h);
                                            if (!f) return <td key={h}>—</td>;
                                            const chg = f.basePrice ? (f.forecastPrice / f.basePrice - 1) * 100 : 0;
                                            return (
                                                <td key={h}>
                                                    <strong>{currencyFormat(f.forecastPrice)}</strong>{' '}
                                                    <span className={`valuation ${chg >= 0 ? 'valuation--up' : 'valuation--down'}`}>
                                                        {chg >= 0 ? '+' : ''}{chg.toFixed(0)}%
                                                    </span>
                                                    <div className="est-note">{currencyFormat(f.low)}–{currencyFormat(f.high)}</div>
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                    <div className="est-note" style={{ marginTop: '0.5rem', maxWidth: '560px' }}>
                        Each horizon is a separate model estimate, so 6- and 12-month figures can differ.
                        The range is a confidence band; confidence reflects how much price history the card
                        has. Cards with limited history (e.g. recent alternate arts) are far less reliable.
                        Not investment advice.
                    </div>
                </section>
            )}
        </div>
        
        </>
    )
}
