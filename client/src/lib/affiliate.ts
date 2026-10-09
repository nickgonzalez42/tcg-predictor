// TCGplayer affiliate clickthrough via Impact (program verified 2026-09-27,
// meta tag in index.html). TRACKING_BASE is the account-specific Impact
// tracking link WITHOUT its ?u= parameter — in Impact: Brands → TCGplayer →
// Create a Link, generate a link for any URL, and strip everything from
// "?u=" on. Empty string = no buy buttons render anywhere (safe dormant
// state until the real link is pasted in).
const TRACKING_BASE = "";
// e.g. "https://tcgplayer.pxf.io/c/1234567/1830156/21018"

export function tcgBuyUrl(productId: number): string | null {
    if (!TRACKING_BASE) return null;
    const target = `https://www.tcgplayer.com/product/${productId}`;
    return `${TRACKING_BASE}?u=${encodeURIComponent(target)}`;
}
