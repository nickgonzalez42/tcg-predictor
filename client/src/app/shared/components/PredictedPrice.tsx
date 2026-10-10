import { currencyFormat } from "../../../lib/util";

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
    if (price == null) return <>—</>;
    return (
        <span className={`predicted${compact ? ' predicted--compact' : ''}`}>
            <span className="predicted__row">
                <span className="predicted__price"
                    title="Predicted launch price: a model estimate from the card's traits — there are no sales yet">
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

// "Releases Oct 16 · in 6 days" / "Released Oct 9 · no market price yet".
export function releaseLabel(iso?: string, short = false): string {
    if (!iso) return '';
    const d = new Date(iso.slice(0, 10) + 'T00:00:00');
    if (isNaN(d.getTime())) return '';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((d.getTime() - today.getTime()) / 86400e3);
    const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (days > 1) return short ? `Releases ${date}` : `Releases ${date} · in ${days} days`;
    if (days === 1) return 'Releases tomorrow';
    if (days === 0) return 'Releases today';
    return short ? `Released ${date}` : `Released ${date} · no market price yet`;
}
