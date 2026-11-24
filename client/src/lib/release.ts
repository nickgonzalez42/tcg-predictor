// When a card's set releases, in words (moved out of PredictedPrice.tsx so
// that file exports its component alone).

// True while the card's set hasn't released yet — the real "pre-release"
// state. False once the date passes (or is missing): released, just without
// a recorded sale yet (e.g. an unsold chase card), which labels differently.
export function isUpcoming(iso?: string): boolean {
    if (!iso) return false;
    const d = new Date(iso.slice(0, 10) + 'T00:00:00');
    if (isNaN(d.getTime())) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    // Release day counts as upcoming: the label beside it says "Releases
    // today", and first sales won't have posted yet anyway.
    return d.getTime() >= today.getTime();
}

// "Releases Oct 16 · in 6 days" / "Released Oct 9 · no market price yet".
export function releaseLabel(iso?: string, short = false): string {
    if (!iso) return '';
    const d = new Date(iso.slice(0, 10) + 'T00:00:00');
    if (isNaN(d.getTime())) return '';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((d.getTime() - today.getTime()) / 86400e3);
    const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (days > 1) return short ? `Releases ${date}` : `Releases ${date} · in ${days} days`;
    if (days === 1) return 'Releases tomorrow';
    if (days === 0) return 'Releases today';
    return short ? `Released ${date}` : `Released ${date} · no market price yet`;
}
