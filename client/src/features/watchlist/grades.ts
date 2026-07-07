// Condition vocabulary for an owned copy. '' / undefined means "unspecified".
// This is distinct from the catalog's "Price shown" list, where '' means Near Mint.
export const OWNED_CONDITIONS = [
    { value: 'nm', label: 'Near Mint' },
    { value: 'lp', label: 'Lightly Played' },
    { value: 'mp', label: 'Moderately Played' },
    { value: 'grade7', label: 'Grade 7' },
    { value: 'grade8', label: 'Grade 8' },
    { value: 'grade9', label: 'Grade 9' },
    { value: 'grade95', label: 'Grade 9.5' },
    { value: 'psa10', label: 'PSA 10' },
];

const LABELS: Record<string, string> = {
    '': 'Unspecified',
    ...Object.fromEntries(OWNED_CONDITIONS.map(o => [o.value, o.label])),
};

export function conditionLabel(grade?: string): string {
    return LABELS[grade ?? ''] ?? grade ?? 'Unspecified';
}

// Catalog "Price shown" grade ('' = Near Mint) -> owned copy condition value.
export function catalogGradeToCondition(grade?: string): string {
    return grade ? grade : 'nm';
}
