import { cardBackSrc } from "../../../lib/cardImages";

// Loading indicator: a card back spinning with the same 3D animation as the
// catalog tile reveal, centered in whatever area is still loading. Pass the
// game to show its back; defaults to the generic back.
export default function CardLoader({ game = '' }: { game?: string }) {
    const back = cardBackSrc(game);
    return (
        <div className="card-loader full-span" role="status" aria-label="Loading">
            <div className="card-loader__inner">
                <img className="card-loader__face" src={back} alt="" />
                <div className="card-loader__core" />
                <img className="card-loader__face card-loader__face--back" src={back} alt="" />
            </div>
        </div>
    );
}
