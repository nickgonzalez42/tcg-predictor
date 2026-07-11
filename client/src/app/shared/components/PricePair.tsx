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
    return (
        <span className="pricepair">
            <span className="pricepair__row">
                <span className="pricepair__now" title="Current price">{currencyFormat(price)}</span>
                {asOf && <span className="mono pricepair__asof">{shortDate(asOf)}</span>}
            </span>
            {forecast != null && (
                <span
                    className={`pricepair__fcst ${forecast >= price ? 'pricepair__fcst--up' : 'pricepair__fcst--down'}`}
                    title={`Model's ${horizon} forecast price`}
                >
                    ({currencyFormat(forecast)})<span className="mono pricepair__tag">{horizon} FCST</span>
                </span>
            )}
        </span>
    );
}
