// The games the site serves — mirrors the API's GameRegistry and pipeline/games.py.
// value = the canonical key used in routes/params; label = what users see.
// Alphabetical by label: this order IS the display order everywhere (catalog
// filter, portfolio/watchlist toggles).
export const GAMES = [
    { value: 'cyberpunk', label: 'Cyberpunk' },
    { value: 'digimon', label: 'Digimon' },
    { value: 'fleshandblood', label: 'Flesh and Blood' },
    { value: 'gundam', label: 'Gundam' },
    { value: 'lorcana', label: 'Lorcana' },
    { value: 'magic', label: 'Magic' },
    { value: 'onepiece', label: 'One Piece' },
    { value: 'pokemon', label: 'Pokémon' },
    { value: 'riftbound', label: 'Riftbound' },
    { value: 'sorcery', label: 'Sorcery' },
    { value: 'starwars', label: 'Star Wars Unlimited' },
    { value: 'yugioh', label: 'Yu-Gi-Oh!' },
];

export const GAME_LABEL: Record<string, string> = Object.fromEntries(
    GAMES.map(g => [g.value, g.label]));

// One colour per game, keyed by canonical key (the seven AllocationDonut has
// always used, plus one each for the games added since). Look up by key or
// label via gameColor().
export const GAME_COLOR: Record<string, string> = {
    onepiece: '#3d7dca', pokemon: '#ffcb05', yugioh: '#c678dd', magic: '#ff9e64',
    lorcana: '#4dd0e1', digimon: '#3fd98a', gundam: '#f06292', starwars: '#e8ecf4',
    fleshandblood: '#e05c5c', sorcery: '#9ccc65', riftbound: '#5a9cf0', cyberpunk: '#f0b429',
};

export function gameColor(game: string): string {
    return GAME_COLOR[gameKey(game)] ?? '#8b96ad';
}

// True when this string names a game we price, by key or by display label.
// gameKey() guesses (it falls back to One Piece), so anything that has to
// tell a game from other text asks here first.
export function isGame(game: string): boolean {
    const needle = game.trim().toLowerCase();
    return GAMES.some(g => g.value === needle || g.label.toLowerCase() === needle);
}

// Canonical key from either a key or a display label ("Yu-Gi-Oh!" -> "yugioh").
export function gameKey(game: string): string {
    const needle = game.trim().toLowerCase();
    const hit = GAMES.find(g =>
        g.value === needle || g.label.toLowerCase() === needle);
    if (hit) return hit.value;
    if (needle.includes('pok')) return 'pokemon';
    if (needle.includes('gi-oh') || needle.includes('gioh')) return 'yugioh';
    return 'onepiece';
}
