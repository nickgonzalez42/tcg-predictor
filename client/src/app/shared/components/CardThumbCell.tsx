import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Card } from "../../models/card";
import { fallbackToCardBack } from "../../../lib/cardImages";

const POP_W = 180;   // keep in sync with .peek__pop width in tables.css
const GAP = 8;

// Table cell with a card thumbnail that pops a larger "peek" image on hover.
// The popup is position:fixed, JS-placed from the thumb's viewport rect, and
// PORTALED to <body>: in-flow it would sit inside the ScrollSmoother transform
// (which re-anchors fixed positioning, drifting it by the scroll offset) and
// inside the table's overflow-x wrapper (which would clip it).
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
                {pos && createPortal(
                    <img className="peek__pop" src={card.pictureUrl} alt="" loading="lazy"
                        style={{ top: pos.top, left: pos.left }}
                        onError={e => fallbackToCardBack(e, card.game, card.cardType)} />,
                    document.body
                )}
            </span>
        </td>
    );
}
