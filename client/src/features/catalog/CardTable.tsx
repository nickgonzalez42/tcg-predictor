import { useNavigate } from "react-router-dom";
import type { Card } from "../../app/models/card";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import TrackButton from "../watchlist/TrackButton";
import { tierLabel } from "../watchlist/grades";
import { confidence } from "./confidence";
import CardThumbCell from "../../app/shared/components/CardThumbCell";

type Props = {
    cards: Card[]
    ownGrade?: string   // selected price tier ('' = ungraded); quick "Own" adds at this tier
}

// Screener-style rows view of the catalog. Row click opens the card; the
// action buttons live in their own cell and don't bubble.
export default function CardTable({ cards, ownGrade }: Props) {
    const navigate = useNavigate();

    return (
        <div className="screener-wrap full-span">
            <table className="screener">
                <thead>
                    <tr>
                        <th aria-label="Card image" />
                        <th>Card</th>
                        <th>Set / Rarity</th>
                        <th className="screener__num">
                            {tierLabel(ownGrade)} price
                        </th>
                        <th className="screener__num">6m fcst</th>
                        <th className="screener__num">12m fcst</th>
                        <th title="Actual price history over the selected window">
                            Past {(cards[0]?.trendPeriod ?? '1m').toUpperCase()}
                        </th>
                        <th>Conf.</th>
                        <th aria-label="Actions" />
                    </tr>
                </thead>
                <tbody>
                    {cards.map(card => {
                        const conf = confidence(card.fcstConfidence, card.historyMonths);
                        const detailPath = `/catalog/${gameKey(card.game)}/${card.id}`;
                        return (
                            <tr key={card.id} className="screener__row" onClick={() => navigate(detailPath)}>
                                <CardThumbCell card={card} />
                                <td className="screener__name">{card.name}</td>
                                <td><span className="mono">{[card.setName, card.rarity].filter(Boolean).join(' · ')}</span></td>
                                <td className="screener__num screener__price">
                                    {card.price != null ? currencyFormat(card.price) : '—'}
                                    {card.priceAsOf && (
                                        <div className="mono price-asof">{shortDate(card.priceAsOf)}</div>
                                    )}
                                </td>
                                <td className="screener__num"><ChangePill value={card.fcst6Pct} title="6 month model forecast" /></td>
                                <td className="screener__num"><ChangePill value={card.fcst12Pct} title="12 month model forecast" /></td>
                                <td><Sparkline points={card.sparkline} /></td>
                                <td>
                                    {card.fcst12Pct != null
                                        ? <span className={`conf ${conf.cls}`} title={conf.reason}>{conf.short}</span>
                                        : <span className="mono">—</span>}
                                </td>
                                <td className="screener__actions" onClick={e => e.stopPropagation()}>
                                    <TrackButton game={gameKey(card.game)} productId={card.id} ownGrade={ownGrade} compact />
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
