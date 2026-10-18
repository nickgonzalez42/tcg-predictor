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
    { value: 'pokemonjp', label: 'Pokémon Japan' },
    { value: 'riftbound', label: 'Riftbound' },
    { value: 'starwars', label: 'Star Wars Unlimited' },
    { value: 'yugioh', label: 'Yu-Gi-Oh!' },
];

export const GAME_LABEL: Record<string, string> = Object.fromEntries(
    GAMES.map(g => [g.value, g.label]));

// Canonical key from either a key or a display label ("Yu-Gi-Oh!" -> "yugioh").
export function gameKey(game: string): string {
    const needle = game.trim().toLowerCase();
    const hit = GAMES.find(g =>
        g.value === needle || g.label.toLowerCase() === needle);
    if (hit) return hit.value;
    // Japanese Pokémon is its OWN catalog, so it has to be recognized before
    // the loose 'pok' fallback below — otherwise "Pokemon Japan" (written
    // without the accent, so the label lookup above misses) would silently
    // resolve to the English game.
    if (needle.includes('pok') && (needle.includes('japan') || needle.includes('jp')))
        return 'pokemonjp';
    if (needle.includes('pok')) return 'pokemon';
    if (needle.includes('gi-oh') || needle.includes('gioh')) return 'yugioh';
    return 'onepiece';
}
