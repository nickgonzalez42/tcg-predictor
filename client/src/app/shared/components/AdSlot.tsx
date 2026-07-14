import { useEffect } from "react";
import { ADSENSE_CLIENT } from "../../../lib/adsense";

declare global {
    interface Window { adsbygoogle?: unknown[] }
}

// One responsive AdSense unit. Renders nothing while ADSENSE_CLIENT is unset
// (pre-approval) so layouts are unaffected. `slot` is the ad-unit id created
// in the AdSense dashboard after approval.
export default function AdSlot({ slot }: { slot: string }) {
    useEffect(() => {
        if (!ADSENSE_CLIENT || !slot) return;
        try {
            (window.adsbygoogle = window.adsbygoogle || []).push({});
        } catch { /* blocked or double-push — nothing to do */ }
    }, [slot]);

    if (!ADSENSE_CLIENT || !slot) return null;

    return (
        <div className="ad-slot">
            <span className="ad-slot__tag mono">Advertisement</span>
            <ins className="adsbygoogle"
                style={{ display: 'block' }}
                data-ad-client={ADSENSE_CLIENT}
                data-ad-slot={slot}
                data-ad-format="auto"
                data-full-width-responsive="true" />
        </div>
    );
}
