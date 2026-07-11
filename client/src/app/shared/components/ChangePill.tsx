import { currencyFormat } from "../../../lib/util";

// Signed change pill: green up / red down, mono, always signed (+6.0% / −1.8%).
type Props = {
    value?: number | null
    unit?: 'percent' | 'usd'
    digits?: number
    title?: string
    suffix?: string   // trailing words inside the pill, e.g. "this month"
}

export default function ChangePill({ value, unit = 'percent', digits = 1, title, suffix }: Props) {
    if (value == null || !isFinite(value)) return <span className="mono">—</span>;
    const up = value >= 0;
    const text = unit === 'usd'
        ? `${up ? '+' : '−'}${currencyFormat(Math.abs(value))}`
        : `${up ? '+' : '−'}${Math.abs(value).toFixed(digits)}%`;

    return (
        <span className={`valuation ${up ? 'valuation--up' : 'valuation--down'}`} title={title}>
            {text}{suffix ? ` ${suffix}` : ''}
        </span>
    );
}
