// TCGplayer affiliate clickthrough via Impact (program verified 2026-09-27,
// meta tag in index.html). TRACKING_BASE is the account-specific Impact
// tracking link WITHOUT its ?u= parameter — in Impact: Brands → TCGplayer →
// Create a Link, generate a link for any URL, and strip everything from
// "?u=" on. Empty string = no buy buttons render anywhere (safe dormant
// state until the real link is pasted in).
// Verified 2026-10-09: redirects to the exact ?u= product page carrying
// irclickid + irpid=7852100 (this account) — clicks track, cards deep-link.
const TRACKING_BASE = "https://partner.tcgplayer.com/c/7852100/1830156/21018";

export function tcgBuyUrl(productId: number): string | null {
    if (!TRACKING_BASE) return null;
    const target = `https://www.tcgplayer.com/product/${productId}`;
    return `${TRACKING_BASE}?u=${encodeURIComponent(target)}`;
}

// Any outbound URL: tcgplayer.com destinations route through the affiliate
// deep link (user storefronts, search links, …); everything else passes
// through untouched. partner.tcgplayer.com is already an affiliate link.
export function affiliateWrap(url: string): string {
    if (!TRACKING_BASE || !url) return url;
    try {
        const h = new URL(url).hostname.toLowerCase();
        const isTcg = h === 'tcgplayer.com' || h.endsWith('.tcgplayer.com');
        if (isTcg && h !== 'partner.tcgplayer.com')
            return `${TRACKING_BASE}?u=${encodeURIComponent(url)}`;
    } catch { /* not an absolute URL — leave it alone */ }
    return url;
}
