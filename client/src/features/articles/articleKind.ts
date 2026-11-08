import type { ArticleFocus, ArticleKind } from "./articlesApi";

// The two article kinds, with the chip colour the pipeline's hero uses for
// each (report_thumbnail.py KIND_COLOR), so the index chip and the picture
// agree.
export const ARTICLE_KINDS: Record<ArticleKind, { label: string; long: string; color: string }> = {
    set: { label: 'Set', long: 'Set report', color: '#4dd0e1' },
    game: { label: 'Game', long: 'Game report', color: '#ff9e64' },
};

// The set slot's other focus (2026-11-07): the game's other recent releases.
// Same slot, same colour as the set report, its own name.
const RECENT = { label: 'New releases', long: 'New releases', color: ARTICLE_KINDS.set.color };

function kindMeta(kind: ArticleKind, focus?: ArticleFocus | null) {
    if (kind === 'set' && focus === 'recent') return RECENT;
    return ARTICLE_KINDS[kind] ?? ARTICLE_KINDS.game;
}

export function articleKindLabel(kind: ArticleKind, short = false, focus?: ArticleFocus | null) {
    const meta = kindMeta(kind, focus);
    return short ? meta.label : meta.long;
}

// Tint and ring from one colour, like reportChipStyle.
export function articleChipStyle(kind: ArticleKind, focus?: ArticleFocus | null) {
    const { color } = kindMeta(kind, focus);
    return {
        color,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        borderColor: `color-mix(in srgb, ${color} 55%, transparent)`,
    };
}
