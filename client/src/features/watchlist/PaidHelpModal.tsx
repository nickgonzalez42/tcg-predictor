import Modal from "../../app/shared/components/Modal";

// Explains the cost-basis rules behind the positions table's Paid column.
export default function PaidHelpModal({ onClose }: { onClose: () => void }) {
    return (
        <Modal title="How 'Paid' works" onClose={onClose}>
            <p>
                <strong>Paid</strong> is what a copy cost you. Your gains and the S&amp;P
                comparison are both measured against it.
            </p>
            <p>
                <strong>Auto price (the default).</strong> We fill in Paid with the card's
                market price, at its condition, on the day you got it. Change the date or
                the condition and it updates. If our prices don't go back that far, Paid
                is $0 and the whole current value counts as gain.
            </p>
            <p>
                <strong>Set it yourself.</strong> Click a row to open its copies, untick
                <em>Auto price</em>, and type the real amount. Do this whenever you know
                what you actually paid. It keeps your gains honest.
            </p>
            <p>
                <strong>Pulled it from a pack?</strong> A fair cost is the pack price
                (usually $4 to $6). Give it to the best card you pulled and leave the rest
                at $0, or split it across the cards you kept: a $5 pack and five keepers is
                $1 each.
            </p>
            <p>
                <strong>Pulled it from a box?</strong> Divide the box price by the number
                of packs (a $90 box of 24 packs is about $3.75 a pack), then do the same
                thing: the per-pack cost on each notable pull, $0 on the rest.
            </p>
        </Modal>
    );
}
