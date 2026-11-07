import type { ArticleKind } from "./articlesApi";

// The two article kinds, with the chip colour the pipeline's hero uses for
// each (report_thumbnail.py KIND_COLOR), so the index chip and the picture
// agree.
export const ARTICLE_KINDS: Record<ArticleKind, { label: string; long: string; color: string }> = {
    set: { label: 'Set', long: 'Set report', color: '#4dd0e1' },
    game: { label: 'Game', long: 'Game report', color: '#ff9e64' },
};

export const ARTICLE_KIND_ORDER = Object.keys(ARTICLE_KINDS) as ArticleKind[];

export function articleKindLabel(kind: ArticleKind, short = false) {
    const meta = ARTICLE_KINDS[kind] ?? ARTICLE_KINDS.game;
    return short ? meta.label : meta.long;
}

// Tint and ring from one colour, like reportChipStyle.
export function articleChipStyle(kind: ArticleKind) {
    const { color } = ARTICLE_KINDS[kind] ?? ARTICLE_KINDS.game;
    return {
        color,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        borderColor: `color-mix(in srgb, ${color} 55%, transparent)`,
    };
}
