import { useRef, useState } from "react";
import type { Card } from "../../models/card";
import { fallbackToCardBack } from "../../../lib/cardImages";

const POP_W = 180;   // keep in sync with .peek__pop width in tables.css
const GAP = 8;

// Table cell with a card thumbnail that pops a larger "peek" image on hover.
// The popup is position:fixed and JS-placed (relative to the viewport) so it
// escapes the table's overflow-x scroll wrapper, which would otherwise clip it
// (overflow-x:auto forces overflow-y to auto, so an absolute popup gets cut off).
export default function CardThumbCell({ card }: { card: Card }) {
    const thumbRef = useRef<HTMLImageElement>(null);
    const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

    const show = () => {
        const el = thumbRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        // Prefer the thumb's right; flip to its left if the popup would run off
        // the viewport's right edge.
        const right = r.right + GAP;
        const left = right + POP_W <= window.innerWidth ? right : r.left - GAP - POP_W;
        setPos({ top: r.top + r.height / 2, left });
    };

    return (
        <td className="screener__thumbcell">
            <span className="peek">
                <img
                    ref={thumbRef}
                    className="screener__thumb"
                    src={card.pictureUrl} alt=""
                    loading="lazy"
                    onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                    onMouseEnter={show}
                    onMouseLeave={() => setPos(null)}
                />
                {pos && (
                    <img className="peek__pop" src={card.pictureUrl} alt="" loading="lazy"
                        style={{ top: pos.top, left: pos.left }}
                        onError={e => fallbackToCardBack(e, card.game, card.cardType)} />
                )}
            </span>
        </td>
    );
}
