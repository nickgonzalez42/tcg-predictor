// Google AdSense integration. Everything is gated on ADSENSE_CLIENT: while it
// is empty the site loads no Google script and renders no ad slots. An ad
// slot stays invisible until Google actually fills it (see AdSlot), so an
// unapproved or unfilled unit never shows an empty box. The consent bar
// that briefly gated the loader (2026-10-25) was removed on 2026-10-26.
//
// Go-live checklist:
//   1. Apply at adsense.google.com with the domain; add the site.
//   2. Paste the publisher id here (ca-pub-XXXXXXXXXXXXXXXX).
//   3. Put the same id in client/public/ads.txt.
//   4. After approval, create ad units and put their slot ids in the
//      <AdSlot slot="..."/> placements (Catalog, CardDetails).
export const ADSENSE_CLIENT = "ca-pub-3270328826867583";   // matches public/ads.txt (2026-09-26)

// Off until AdSense approves the site (2026-10-29). The loader and its
// traffic-quality call cost about 250 KB and three third-party connections
// in the first seconds of every page view, and with the application
// rejected they earn nothing. Flip to true after approval; the publisher id
// and the slots stay wired. The static page generators carry the same
// switch (report_pages.py, set_pages.py, prerender_pages.py).
export const ADSENSE_ENABLED = false;

let loaded = false;

// Inject the AdSense loader once (no-op while disabled or unconfigured).
export function initAdsense() {
    if (!ADSENSE_ENABLED || !ADSENSE_CLIENT || loaded) return;
    loaded = true;
    const s = document.createElement("script");
    s.async = true;
    s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;
    s.crossOrigin = "anonymous";
    document.head.appendChild(s);
}
