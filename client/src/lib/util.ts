export function currencyFormat(amount?: number) {
    if (amount === null || amount === undefined) return 'N/A';
    return '$' + amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Canonical game key (re-exported so existing imports keep working).
export { gameKey } from './games';

// "2026-07-01..." -> "Jul 1, 2026" (returns the input if it doesn't parse).
export function shortDate(date?: string) {
    if (!date) return '';
    const d = new Date(date.slice(0, 10) + 'T00:00:00');
    return isNaN(d.getTime())
        ? date
        : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function filterEmptyValues(values: object) {
    return Object.fromEntries(
        Object.entries(values).filter(
            ([, value]) => value !== '' && value !== null && value !== undefined && value.length !== 0
        )
    )
}

// CardParams fields that are presentation state and must never reach the API.
const CLIENT_ONLY_PARAMS = ['view', 'gameInitialized'];

// Query-string payload for a card list request: drops client-only fields and
// empty values in one place.
export function toApiParams(params: object) {
    return filterEmptyValues(Object.fromEntries(
        Object.entries(params).filter(([key]) => !CLIENT_ONLY_PARAMS.includes(key))
    ));
}
