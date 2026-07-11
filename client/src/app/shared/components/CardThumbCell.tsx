import type { Card } from "../../models/card";
import { fallbackToCardBack } from "../../../lib/cardImages";

// Table cell with a card thumbnail that pops a larger "peek" image on hover.
export default function CardThumbCell({ card }: { card: Card }) {
    return (
        <td className="screener__thumbcell">
            <span className="peek">
                <img
                    className="screener__thumb"
                    src={card.pictureUrl} alt=""
                    loading="lazy"
                    onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                />
                <img className="peek__pop" src={card.pictureUrl} alt="" loading="lazy" />
            </span>
        </td>
    );
}
