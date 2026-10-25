// Google AdSense integration, gated twice (2026-10-25 launch review):
//  1. ADSENSE_CLIENT: while empty the site loads no Google script at all.
//  2. Visitor consent: the loader is injected only after the visitor accepts
//     ads in the consent bar (stored in localStorage); declining leaves the
//     page ad-free. Slots stay invisible until Google actually fills them,
//     so an unapproved or unfilled unit never shows an empty box.
//
// Go-live checklist:
//   1. Apply at adsense.google.com with the domain; add the site.
//   2. Paste the publisher id here (ca-pub-XXXXXXXXXXXXXXXX).
//   3. Put the same id in client/public/ads.txt.
//   4. After approval, create ad units and put their slot ids in the
//      <AdSlot slot="..."/> placements (Catalog, CardDetails).
export const ADSENSE_CLIENT = "ca-pub-3270328826867583";   // matches index.html + public/ads.txt (2026-09-26)

const CONSENT_KEY = 'ads-consent';   // 'yes' | 'no' | absent (not asked yet)
type Consent = 'yes' | 'no' | null;
const listeners = new Set<(c: Consent) => void>();

export function adsConsent(): Consent {
    try {
        const v = localStorage.getItem(CONSENT_KEY);
        return v === 'yes' || v === 'no' ? v : null;
    } catch { return null; }
}

export function setAdsConsent(c: 'yes' | 'no') {
    try { localStorage.setItem(CONSENT_KEY, c); } catch { /* private mode: the choice lasts the session */ }
    if (c === 'yes') initAdsense();
    listeners.forEach(fn => fn(c));
}

export function onAdsConsent(fn: (c: Consent) => void) {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
}

let loaded = false;

// Inject the AdSense loader once — only with a publisher id and consent.
export function initAdsense() {
    if (!ADSENSE_CLIENT || loaded || adsConsent() !== 'yes') return;
    loaded = true;
    const s = document.createElement("script");
    s.async = true;
    s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;
    s.crossOrigin = "anonymous";
    document.head.appendChild(s);
}
