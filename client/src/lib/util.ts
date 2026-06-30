export function currencyFormat(amount?: number) {
    if (amount === null || amount === undefined) return 'N/A';
    return '$' + amount.toFixed(2);
}

export function gameKey(game: string) {
    return game.toLowerCase().includes('pok') ? 'pokemon' : 'onepiece';
}

export function filterEmptyValues(values: object) {
    return Object.fromEntries(
        Object.entries(values).filter(
            ([, value]) => value !== '' && value !== null && value !== undefined && value.length !== 0
        )
    )
}
