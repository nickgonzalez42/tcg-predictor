export function currencyFormat(amount?: number) {
    if (amount === null || amount === undefined) return 'N/A';
    return '$' + amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function gameKey(game: string) {
    return game.toLowerCase().includes('pok') ? 'pokemon' : 'onepiece';
}

// How the model's estimate compares to the market price, as a percentage.
// Positive = model values the card above its current market price.
export function pctVsMarket(actual?: number, predicted?: number): number | null {
    if (predicted == null || actual == null || actual === 0) return null;
    return ((predicted - actual) / actual) * 100;
}

export function filterEmptyValues(values: object) {
    return Object.fromEntries(
        Object.entries(values).filter(
            ([, value]) => value !== '' && value !== null && value !== undefined && value.length !== 0
        )
    )
}
