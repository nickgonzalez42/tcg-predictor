// Price/condition tiers — the PriceCharting vocabulary, used everywhere a card
// is priced, added, or displayed with a condition. PriceCharting prices raw
// cards as a single "ungraded" series (no played-condition tiers) plus the
// graded ladder, so '' doubles as both the ungraded price tier and the grade
// value of a raw owned copy (stored as NULL server-side).
export const PRICE_TIER_OPTIONS = [
    { value: '', label: 'Ungraded' },
    { value: 'grade7', label: 'Grade 7' },
    { value: 'grade8', label: 'Grade 8' },
    { value: 'grade9', label: 'Grade 9' },
    { value: 'grade95', label: 'Grade 9.5' },
    { value: 'psa10', label: 'PSA 10' },
];

// Label for a tier value; an unknown legacy value renders as-is.
export function tierLabel(grade?: string): string {
    return PRICE_TIER_OPTIONS.find(o => o.value === (grade ?? ''))?.label ?? grade!;
}
