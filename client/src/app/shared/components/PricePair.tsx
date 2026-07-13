import { currencyFormat, shortDate } from "../../../lib/util";

// Current price (brand yellow/gold) with its date beside it, and the model's
// forecast price on the line below — smaller, parenthesized, green when above
// current / red when below, tagged with the forecast horizon so it's never
// mistaken for history.
type Props = {
    price?: number | null
    forecast?: number | null
    horizon?: string   // e.g. "12M" — defaults to 12M, the longest trained horizon
    asOf?: string      // date of the price's latest history point
}

export default function PricePair({ price, forecast, horizon = '12M', asOf }: Props) {
    if (price == null) return <>—</>;
    // The 12-month horizon reads as "1Y" in the UI (its own trend window label).
    const label = horizon === '12M' ? '1Y' : horizon;
    return (
        <span className="pricepair">
            <span className="pricepair__row">
                <span className="pricepair__now" title="Current price">{currencyFormat(price)}</span>
                {asOf && <span className="mono pricepair__asof">{shortDate(asOf)}</span>}
            </span>
            {forecast != null && (
                <span
                    className={`pricepair__fcst ${forecast >= price ? 'pricepair__fcst--up' : 'pricepair__fcst--down'}`}
                    title={`Model's ${label} forecast price`}
                >
                    ({currencyFormat(forecast)})<span className="mono pricepair__tag">{label} FCST</span>
                </span>
            )}
        </span>
    );
}
