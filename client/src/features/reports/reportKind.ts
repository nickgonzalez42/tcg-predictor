// Market report vs model report: the kind lives in the slug prefix the
// pipeline writes (2026-10-28), so the list can filter and each page can tag
// itself without an API or schema change.
export type ReportKind = 'market' | 'model';

export function reportKind(slug: string): ReportKind {
    return slug.startsWith('model-report') ? 'model' : 'market';
}

// `short` is the tag-line form on the index cards ("Market"); the long form
// names the article type in meta tags and prose.
export function reportKindLabel(slug: string, short = false) {
    const model = reportKind(slug) === 'model';
    if (short) return model ? 'Model' : 'Market';
    return model ? 'Model report' : 'Market report';
}

// "2026-10-23" -> "October 23, 2026" (midday UTC so the date never slips a
// day westward); returns the input unchanged if it doesn't parse.
export function longDate(date?: string) {
    if (!date) return '';
    const d = new Date(date.slice(0, 10) + 'T12:00:00Z');
    return isNaN(d.getTime()) ? date : d.toLocaleDateString('en-US', {
        month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC',
    });
}
