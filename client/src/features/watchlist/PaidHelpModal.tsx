import Modal from "../../app/shared/components/Modal";

// Explains the cost-basis rules behind the positions table's Paid column.
export default function PaidHelpModal({ onClose }: { onClose: () => void }) {
    return (
        <Modal title="How 'Paid' works" onClose={onClose}>
            <p>
                <strong>Paid</strong> is each copy's cost basis, what P/L and the
                S&amp;P comparison measure against.
            </p>
            <p>
                <strong>Auto price (the default).</strong> Each copy's Paid is set to the
                card's market price, at its condition, on the day you acquired it. Change
                the acquired date or grade and it recalculates. If no price data goes back
                that far, Paid is $0, meaning its full current value counts as gain.
            </p>
            <p>
                <strong>Set it yourself.</strong> Open a position's copies (click the row),
                uncheck <em>Auto price</em>, and type the real amount. Use this whenever you
                know what you actually paid. It keeps your P/L honest.
            </p>
            <p>
                <strong>Pulled it from a pack?</strong> A fair basis is the pack price
                (typically $4–6): assign it to the best card you pulled and let the other
                pulls ride at $0, or split it evenly across the cards you kept: a $5 pack
                across five keepers is $1 each.
            </p>
            <p>
                <strong>Pulled it from a box?</strong> Divide the box price by its pack
                count to get a per-pack cost (a $90 booster box of 24 packs is about
                $3.75 per pack), then apply the same idea: per-pack cost on each notable
                pull, $0 on the rest.
            </p>
        </Modal>
    );
}
