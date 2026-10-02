// Price/condition tiers — the PriceCharting vocabulary, used everywhere a card
// is priced, added, or displayed with a condition. PriceCharting prices raw
// cards as a single "ungraded" series (no played-condition tiers) plus the
// graded ladder, so '' doubles as both the ungraded price tier and the grade
// value of a raw owned copy (stored as NULL server-side).

// Every tier, worst to best, with its display label.
export const GRADE_TIERS =
    ['ungraded', 'grade7', 'grade8', 'grade9', 'grade95', 'psa10', 'bgs10', 'cgc10', 'sgc10'];

export const GRADE_TIER_LABEL: Record<string, string> = {
    ungraded: 'Ungraded', grade7: 'Grade 7', grade8: 'Grade 8', grade9: 'Grade 9',
    grade95: 'Grade 9.5', psa10: 'PSA 10', bgs10: 'BGS 10', cgc10: 'CGC 10', sgc10: 'SGC 10',
};

// The subset offered by the "Price shown" filter dropdowns.
export const PRICE_TIER_OPTIONS = [
    { value: '', label: 'Ungraded' },
    { value: 'grade7', label: 'Grade 7' },
    { value: 'grade8', label: 'Grade 8' },
    { value: 'grade9', label: 'Grade 9' },
    { value: 'grade95', label: 'Grade 9.5' },
    { value: 'psa10', label: 'PSA 10' },
];

// Label for a tier value ('' and undefined = ungraded); an unknown legacy
// value renders as-is.
export function tierLabel(grade?: string): string {
    const key = grade || 'ungraded';
    return GRADE_TIER_LABEL[key] ?? key;
}

// Booster-pack MSRP per game (USD, approximate 2026 retail). Pack pulls list
// at this price in the positions table — display only, in the pack accent
// color; it never enters paid P/L, the invested line or the S&P benchmark.
// Tune freely as retail moves.
export const PACK_PRICE: Record<string, number> = {
    pokemon: 4.49,
    onepiece: 4.69,
    magic: 5.49,
    yugioh: 4.49,
    lorcana: 5.99,
    digimon: 4.49,
    gundam: 4.99,
    starwars: 4.99,
};

export function packPrice(game: string): number {
    return PACK_PRICE[game] ?? 4.49;
}
