import type { SyntheticEvent } from "react";
import { gameKey } from "./util";

// Back-of-card art, used both by the 3D flip and as the placeholder when a
// card's local image is missing (images come only from our own scrape — we
// never hotlink TCGplayer). One Piece leaders and DON!! cards have their own
// backs; games without a scanned back share a neutral generic one.
//
// All ten are the real backs as of 2026-10-20. Flesh and Blood is the
// publisher's own print-spec file from cdn.fabtcg.com with the bleed
// cropped off; Cyberpunk and Riftbound were supplied by the user (Riftbound
// is only 250x349, the smallest here, so it softens if a view ever renders
// it large — gundam-back.jpg has the same limitation). Sorcery is the
// Spellbook back from the community wiki: the right design, but a PRE-ALPHA
// print carrying a small "Pre-Alpha 0.5" watermark low on the right — swap
// it if a retail scan turns up. Star Wars Unlimited is the one game with no
// back of its own; it falls through to the generic one.
const BACKS: Record<string, string> = {
    pokemon: '/images/pokemon-back.jpg',
    yugioh: '/images/yu-gi-oh-back.jpg',
    magic: '/images/mtg-back.jpg',
    lorcana: '/images/lorcana-back.png',
    digimon: '/images/digimon-back.webp',
    gundam: '/images/gundam-back.jpg',
    fleshandblood: '/images/fleshandblood-back.jpg',
    sorcery: '/images/sorcery-back.jpg',
    riftbound: '/images/riftbound-back.png',
    cyberpunk: '/images/cyberpunk-back.jpg',
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
