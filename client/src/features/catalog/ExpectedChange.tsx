import type { Card } from "../../app/models/card";
import { currencyFormat } from "../../lib/util";
import ChangePill from "../../app/shared/components/ChangePill";

// Colored expected forecast change, shown in place of the price when the list is
// sorted by a forecast metric. Green up / red down, with the horizon (6m/12m).
export default function ExpectedChange({ card }: { card: Card }) {
    if (card.expectedChange == null) return null;

    return (
        <span className="expected">
            {card.expectedFrom != null && card.expectedTo != null && (
                <span className="expected__prices">
                    {currencyFormat(card.expectedFrom)} <span className="expected__arrow">→</span> {currencyFormat(card.expectedTo)}
                </span>
            )}
            <span className="expected__row">
                <ChangePill value={card.expectedChange} unit={card.expectedUnit ?? 'percent'} />
                {card.expectedHorizon && <span className="expected__horizon">{card.expectedHorizon} forecast</span>}
            </span>
        </span>
    );
}
