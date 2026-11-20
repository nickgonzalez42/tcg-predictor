// The one TCGplayer banner the site shows (above the footer links on every
// page, and under the closing block of the static report and article pages:
// pipeline/static_nav.py carries the same values, change both together).
//
// 2026-11-20, user: TCGplayer's Cyber Weekend creative, in a low-key spot,
// off after 11/30. The images are our own copies in public/promo (no request
// leaves for an ad server until someone clicks); the link is the Impact
// tracking link of the leaderboard creative. The banner removes itself at
// `until` whether or not anyone remembers to take it out.
// The full list of creatives TCGplayer offers this account is kept beside
// the pipeline data: one-piece/ml_data/tcgplayer_ads/.
export const PROMO = {
    id: "cw26",
    href: "https://partner.tcgplayer.com/c/7852100/4128197/21018",
    wide: "/promo/cw26-970x90.jpg",      // 1940 x 180, shown at 970 x 90 at most
    narrow: "/promo/cw26-320x50.jpg",    // 640 x 100, phones
    alt: "TCGplayer Cyber Weekend: 10% store credit, November 27 to 30",
    until: Date.parse("2026-12-01T04:00:00Z"),   // the sale's end, 11/30 11 PM New York
} as const;

export function promoLive(now: number = Date.now()): boolean {
    return !!PROMO.href && now < PROMO.until;
}
