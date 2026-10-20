import type { SyntheticEvent } from "react";
import { gameKey } from "./util";

// Back-of-card art, used both by the 3D flip and as the placeholder when a
// card's local image is missing (images come only from our own scrape — we
// never hotlink TCGplayer). One Piece leaders and DON!! cards have their own
// backs; games without a scanned back share a neutral generic one.
//
// Flesh and Blood (2026-10-20) is the publisher's own print-spec back from
// cdn.fabtcg.com, bleed cropped off. Sorcery is the Spellbook back from the
// community wiki — the right design, but a PRE-ALPHA print with a small
// "Pre-Alpha 0.5" watermark low on the right; swap it if a retail scan turns
// up. Riftbound and Cyberpunk have no flat scan published anywhere I could
// find (only angled press photos, and Cyberpunk does not retail until
// November 2026), so they keep ORIGINAL house-style SVG placeholders — not
// reproductions — until one exists. Star Wars Unlimited still has neither
// and falls through to the generic back.
const BACKS: Record<string, string> = {
    pokemon: '/images/pokemon-back.jpg',
    yugioh: '/images/yu-gi-oh-back.jpg',
    magic: '/images/mtg-back.jpg',
    lorcana: '/images/lorcana-back.png',
    digimon: '/images/digimon-back.webp',
    gundam: '/images/gundam-back.jpg',
    fleshandblood: '/images/fleshandblood-back.jpg',
    sorcery: '/images/sorcery-back.jpg',
    riftbound: '/images/riftbound-back.svg',
    cyberpunk: '/images/cyberpunk-back.svg',
};

export function cardBackSrc(game: string, cardType?: string) {
    const key = gameKey(game);
    if (key === 'onepiece') {
        if (cardType === 'Leader') return '/images/one-piece-leader-back.png';
        if (cardType === 'DON!!') return '/images/one-piece-don-card-back.jpg';
        return '/images/one-piece-card-back.jpg';
    }
    return BACKS[key] ?? '/images/generic-card-back.svg';
}

// onError handler: swap a broken card image for the card-back placeholder.
export function fallbackToCardBack(e: SyntheticEvent<HTMLImageElement>, game: string, cardType?: string) {
    const img = e.currentTarget;
    const back = cardBackSrc(game, cardType);
    if (!img.src.endsWith(back)) img.src = back;
    else img.onerror = null;   // even the back failed — stop retrying
}
