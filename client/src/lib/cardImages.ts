import type { SyntheticEvent } from "react";
import { gameKey } from "./util";

// Back-of-card art, used both by the 3D flip and as the placeholder when a
// card's local image is missing (images come only from our own scrape — we
// never hotlink TCGplayer). One Piece leaders and DON!! cards have their own
// backs; games without a scanned back share a neutral generic one.
export function cardBackSrc(game: string, cardType?: string) {
    const key = gameKey(game);
    if (key === 'pokemon') return '/images/pokemon-back.jpg';
    if (key === 'yugioh') return '/images/yu-gi-oh-back.jpg';
    if (key !== 'onepiece') return '/images/generic-card-back.svg';
    if (cardType === 'Leader') return '/images/one-piece-leader-back.png';
    if (cardType === 'DON!!') return '/images/one-piece-don-card-back.jpg';
    return '/images/one-piece-card-back.jpg';
}

// onError handler: swap a broken card image for the card-back placeholder.
export function fallbackToCardBack(e: SyntheticEvent<HTMLImageElement>, game: string, cardType?: string) {
    const img = e.currentTarget;
    const back = cardBackSrc(game, cardType);
    if (!img.src.endsWith(back)) img.src = back;
    else img.onerror = null;   // even the back failed — stop retrying
}
