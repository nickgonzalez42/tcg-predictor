// First-party page-view beacon (2026-10-27).
//
// The server's access log is the only record of who visits, but this is a
// single-page app: clicking from a card to the catalog changes the URL
// without asking the server for anything, so those views are invisible. A
// one-pixel-of-work ping to /px fixes that. Caddy answers it with 204
// itself — the API is never involved — and the request lands in the same
// access log the traffic report reads.
//
// No cookie, no third-party script, no identifier of any kind: the log sees
// exactly what it would have seen for a normal request.

let lastSent = "";

export function trackPageView(path: string) {
    if (!path || path === lastSent) return;      // StrictMode double-render, same-path replaces
    lastSent = path;
    try {
        const q = new URLSearchParams({ p: path });
        // Referrer only on the first view of a visit, and only when it came
        // from somewhere else — in-site referrers say nothing.
        const ref = document.referrer;
        if (ref && !ref.includes(window.location.host)) q.set("r", ref);
        // Campaign tags ride along so a Reddit ad can be told from organic.
        const utm = window.location.search.match(/utm_[a-z]+=[^&]+/g);
        if (utm) q.set("u", utm.join("&"));
        fetch(`/px?${q.toString()}`, { method: "GET", keepalive: true, cache: "no-store" })
            .catch(() => { /* a lost count is not worth a console error */ });
    } catch { /* never let counting break a page */ }
}
