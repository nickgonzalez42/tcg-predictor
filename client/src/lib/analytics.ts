// Cloudflare Web Analytics (2026-10-27): visitor + page numbers with a
// dashboard, no cookies, no consent bar needed, free, and it understands
// single-page-app navigation (it hooks the History API, so moving from a
// card to the catalog counts as a page view).
//
// Dormant until BEACON_TOKEN is set, exactly like lib/adsense.ts: with an
// empty token the site loads nothing from Cloudflare at all.
//
// To turn it on:
//   1. dash.cloudflare.com -> Analytics & Logs -> Web Analytics -> Add a site
//      (the site does NOT need its DNS on Cloudflare; yours stays at Vercel).
//   2. Enter cardstock.guide; Cloudflare shows a snippet containing
//      data-cf-beacon='{"token": "abc123..."}'.
//   3. Paste just that token below and deploy.
export const BEACON_TOKEN = "";

let loaded = false;

export function initAnalytics() {
    if (!BEACON_TOKEN || loaded) return;
    loaded = true;
    const s = document.createElement("script");
    s.defer = true;
    s.src = "https://static.cloudflareinsights.com/beacon.min.js";
    s.setAttribute("data-cf-beacon", JSON.stringify({ token: BEACON_TOKEN }));
    document.head.appendChild(s);
}
