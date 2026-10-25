import { useState } from "react";
import { Link } from "react-router-dom";
import { ADSENSE_CLIENT, adsConsent, setAdsConsent } from "../../../lib/adsense";

// First-visit consent bar for advertising (2026-10-25). Sign-in cookies need
// no consent (strictly necessary); Google ads do, so nothing from Google
// loads until the visitor says yes. The choice is remembered per browser.
export default function ConsentBanner() {
    const [choice, setChoice] = useState(adsConsent());
    if (!ADSENSE_CLIENT || choice !== null) return null;
    const pick = (c: 'yes' | 'no') => { setAdsConsent(c); setChoice(c); };
    return (
        <div className="consent" role="dialog" aria-label="Cookies and ads">
            <p className="consent__text">
                CardStock uses a cookie to keep you signed in. With your permission it also shows
                Google ads, which use cookies of their own. See our <Link to="/privacy">privacy policy</Link>.
            </p>
            <div className="consent__actions">
                <button type="button" className="btn btn--outline" onClick={() => pick('no')}>No ads</button>
                <button type="button" className="btn" onClick={() => pick('yes')}>Allow ads</button>
            </div>
        </div>
    );
}
