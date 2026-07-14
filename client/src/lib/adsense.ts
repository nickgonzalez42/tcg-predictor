// Google AdSense integration. Everything is gated on ADSENSE_CLIENT: while it
// is empty the site loads no Google script and renders no ad slots, so this
// can ship long before the AdSense application is approved.
//
// Go-live checklist (requires the real domain first — AdSense won't accept a
// bare IP):
//   1. Apply at adsense.google.com with the domain; add the site.
//   2. Paste the publisher id here (ca-pub-XXXXXXXXXXXXXXXX).
//   3. Put the same id in client/public/ads.txt (replace the placeholder).
//   4. After approval, create ad units and put their slot ids in the
//      <AdSlot slot="..."/> placements (Catalog, CardDetails).
export const ADSENSE_CLIENT = "";   // e.g. "ca-pub-1234567890123456"

let loaded = false;

// Inject the AdSense loader once (no-op until ADSENSE_CLIENT is set).
export function initAdsense() {
    if (!ADSENSE_CLIENT || loaded) return;
    loaded = true;
    const s = document.createElement("script");
    s.async = true;
    s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;
    s.crossOrigin = "anonymous";
    document.head.appendChild(s);
}
