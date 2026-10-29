import { useEffect, useRef, useState } from "react";
import { ADSENSE_CLIENT, ADSENSE_ENABLED } from "../../../lib/adsense";

declare global {
    interface Window { adsbygoogle?: unknown[] }
}

// One responsive AdSense unit. Renders nothing without a publisher id or a
// slot id, and stays collapsed (no label, no box) until Google reports the
// unit filled — an unapproved site or an unfilled request leaves the layout
// exactly as if the slot weren't there.
export default function AdSlot({ slot }: { slot: string }) {
    const insRef = useRef<HTMLModElement>(null);
    const [filled, setFilled] = useState(false);

    useEffect(() => {
        if (!ADSENSE_ENABLED || !ADSENSE_CLIENT || !slot) return;
        const ins = insRef.current;
        if (!ins) return;
        try {
            (window.adsbygoogle = window.adsbygoogle || []).push({});
        } catch { /* blocked or double-push: nothing to do */ }
        // AdSense stamps data-ad-status="filled" | "unfilled" on the <ins>.
        const check = () => { if (ins.getAttribute('data-ad-status') === 'filled') setFilled(true); };
        check();
        const mo = new MutationObserver(check);
        mo.observe(ins, { attributes: true, attributeFilter: ['data-ad-status'] });
        return () => mo.disconnect();
    }, [slot]);

    if (!ADSENSE_ENABLED || !ADSENSE_CLIENT || !slot) return null;

    return (
        <div className={`ad-slot${filled ? '' : ' ad-slot--pending'}`} aria-hidden={!filled}>
            <span className="ad-slot__tag mono">Advertisement</span>
            <ins ref={insRef} className="adsbygoogle"
                style={{ display: 'block' }}
                data-ad-client={ADSENSE_CLIENT}
                data-ad-slot={slot}
                data-ad-format="auto"
                data-full-width-responsive="true" />
        </div>
    );
}
