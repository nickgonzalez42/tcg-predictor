import { useNavigate } from "react-router-dom";
import type { Card } from "../../app/models/card";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import TrackButton from "../watchlist/TrackButton";
import { tierLabel } from "../watchlist/grades";
import CardThumbCell from "../../app/shared/components/CardThumbCell";

type Props = {
    cards: Card[]
    ownGrade?: string   // selected price tier ('' = ungraded); quick "Own" adds at this tier
    trend?: string      // selected 1w|1m|6m|1y window (drives both change columns)
}

// The forecast horizon each trend window maps to (mirrors the API's
// TrendWindows: the model has no 1y horizon, so 1Y shows the 12m forecast).
export const TREND_FCST: Record<string, string> = { '1w': '1W', '1m': '1M', '6m': '6M', '1y': '12M' };

// Screener-style rows view of the catalog. Row click opens the card; the
// action buttons live in their own cell and don't bubble.
export default function CardTable({ cards, ownGrade, trend }: Props) {
    const navigate = useNavigate();
    const period = (trend ?? '1m').toLowerCase();
    const fcstLabel = TREND_FCST[period] ?? '12M';

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
                        <th className="screener__mid"
                            title="Model forecast over the horizon matching the selected window">
                            {fcstLabel} fcst
                        </th>
                        <th className="screener__mid" title="Actual price history over the selected window">
                            Past {period.toUpperCase()}
                        </th>
                        <th aria-label="Actions" />
                    </tr>
                </thead>
                <tbody>
                    {cards.map(card => {
                        // Forecast % over the horizon the trend buttons snapped to
                        // (fcstTo follows the selected 1W/1M/6M/1Y window).
                        const fcstPct = card.fcstTo != null && card.price
                            ? (card.fcstTo / card.price - 1) * 100 : undefined;
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
                                <td className="screener__mid">
                                    <ChangePill value={fcstPct}
                                        title={`${fcstLabel} model forecast`} />
                                </td>
                                <td className="screener__mid"><Sparkline points={card.sparkline} /></td>
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
