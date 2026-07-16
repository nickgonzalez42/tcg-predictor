import type { CardAlert } from "./watchlistApi";
import { tierLabel } from "./grades";
import { currencyFormat } from "../../lib/util";

// Shared alert copy: the modal's rows and the notifications page describe
// alerts identically.
export const HZ_SHORT: Record<string, string> = { '1w': '1W', '1m': '1M', '6m': '6M', '12m': '1Y' };

export const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;

// "PSA 10 · 1M forecast ≥ $500" — one line describing an alert.
export function describeAlert(a: CardAlert) {
    const dir = a.direction === 'above' ? '≥' : '≤';
    const cond = tierLabel(a.grade ?? '');
    if (a.kind === 'price') return `${cond} price ${dir} ${currencyFormat(a.target)}`;
    const hz = HZ_SHORT[a.horizon ?? ''] ?? a.horizon;
    return a.kind === 'fcst_price'
        ? `${cond} · ${hz} forecast ${dir} ${currencyFormat(a.target)}`
        : `${cond} · ${hz} forecast ${dir} ${pct(a.target)}`;
}

export function alertCurrentLabel(a: CardAlert) {
    if (a.current == null) return 'no data';
    return a.kind === 'fcst_pct' ? `now ${pct(a.current)}` : `now ${currencyFormat(a.current)}`;
}
