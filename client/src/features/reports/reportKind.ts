// What kind of article a report is. The kind lives in the slug prefix the
// pipeline writes (2026-10-28), so the list can filter and colour by it
// without an API or schema change.
//
// Adding a kind is one entry here: it gets a filter chip on the index, its
// own chip colour on every card, and its label in the article's meta tags.
export type ReportKind = 'market' | 'model';

type KindMeta = {
    /** Slug prefix the pipeline writes for this kind. */
    prefix: string;
    /** Chip and filter label. */
    label: string;
    /** Full name, for meta tags and prose. */
    long: string;
    /** Chip colour: tinted for the fill and the ring. */
    color: string;
};

export const REPORT_KINDS: Record<ReportKind, KindMeta> = {
    market: {
        prefix: 'weekly-market-report', label: 'Market',
        long: 'Market report', color: '#7fb0ea',
    },
    model: {
        prefix: 'model-report', label: 'Model',
        long: 'Model report', color: '#c678dd',
    },
};

// Display order for the filter row; also the order kinds are matched in.
export const REPORT_KIND_ORDER = Object.keys(REPORT_KINDS) as ReportKind[];

export function reportKind(slug: string): ReportKind {
    return REPORT_KIND_ORDER.find(k => slug.startsWith(REPORT_KINDS[k].prefix)) ?? 'market';
}

export function reportKindLabel(slug: string, short = false) {
    const meta = REPORT_KINDS[reportKind(slug)];
    return short ? meta.label : meta.long;
}

// Tint and ring from one colour, so a new kind needs no CSS of its own.
export function reportChipStyle(kind: ReportKind) {
    const { color } = REPORT_KINDS[kind];
    return {
        color,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        borderColor: `color-mix(in srgb, ${color} 55%, transparent)`,
    };
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
