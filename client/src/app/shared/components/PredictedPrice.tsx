import { currencyFormat } from "../../../lib/util";
import { releaseLabel } from "../../../lib/release";

// Launch-price ESTIMATE for a card with no market price yet (pre-release).
// Deliberately unlike PricePair: violet, tagged PREDICTED, and always shown
// with its range — nothing here should read as a market price.
type Props = {
    price?: number | null
    low?: number | null
    high?: number | null
    compact?: boolean      // table cells: tighter, range only
    releaseDate?: string   // ISO; rendered as a "Releases …" line when given
}

export default function PredictedPrice({ price, low, high, compact, releaseDate }: Props) {
    if (price == null) return <>n/a</>;
    return (
        <span className={`predicted${compact ? ' predicted--compact' : ''}`}>
            <span className="predicted__row">
                <span className="predicted__price"
                    title="Predicted launch price: a model estimate from the card's traits; there are no sales yet">
                    {currencyFormat(price)}
                </span>
                <span className="mono predicted__tag">PREDICTED</span>
            </span>
            {low != null && high != null && (
                <span className="mono predicted__range"
                    title="Likely range: 80% of past launches landed inside the equivalent band">
                    {currencyFormat(low)}–{currencyFormat(high)}
                </span>
            )}
            {releaseDate && !compact && (
                <span className="mono predicted__release">{releaseLabel(releaseDate)}</span>
            )}
        </span>
    );
}
