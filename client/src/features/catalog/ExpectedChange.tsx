import type { Card } from "../../app/models/card";
import { currencyFormat } from "../../lib/util";

// Colored expected forecast change, shown in place of the price when the list is
// sorted by a forecast metric. Green up / red down, with the horizon (6m/12m).
export default function ExpectedChange({ card }: { card: Card }) {
    if (card.expectedChange == null) return null;
    const v = card.expectedChange;
    const up = v >= 0;
    const text = card.expectedUnit === 'usd'
        ? `${up ? '+' : '−'}${currencyFormat(Math.abs(v))}`
        : `${up ? '+' : ''}${v.toFixed(1)}%`;

    return (
        <span className="expected">
            {card.expectedFrom != null && card.expectedTo != null && (
                <span className="expected__prices">
                    {currencyFormat(card.expectedFrom)} <span className="expected__arrow">→</span> {currencyFormat(card.expectedTo)}
                </span>
            )}
            <span className="expected__row">
                <span className={`valuation ${up ? 'valuation--up' : 'valuation--down'}`}>{text}</span>
                {card.expectedHorizon && <span className="expected__horizon">{card.expectedHorizon} forecast</span>}
            </span>
        </span>
    );
}
