// Forecast confidence, as reported by the model itself: each forecast carries
// the width of the model's own 10th–90th percentile scenario interval, and
// tight = high confidence. The months-of-history heuristic remains only as a
// fallback for rows scored before confidence existed.
const LEVELS: Record<string, { label: string; short: string; cls: string; reason: string }> = {
    high: {
        label: 'High confidence', short: 'HIGH', cls: 'conf--high',
        reason: "Our best-case and worst-case scenarios for this card mostly agree, so this forecast stands on firmer ground.",
    },
    med: {
        label: 'Medium confidence', short: 'MED', cls: 'conf--med',
        reason: "Our range of likely outcomes is fairly wide. Treat the forecast as the middle of a spread, not an exact number.",
    },
    low: {
        label: 'Low confidence', short: 'LOW', cls: 'conf--low',
        reason: "Our range of likely outcomes is wide, which means we're unsure. Pay more attention to the low and high than to the single number.",
    },
};

export function confidence(level?: string, months?: number) {
    if (level && LEVELS[level]) return LEVELS[level];
    // fallback heuristic (pre-confidence data): history depth
    const m = months ?? 0;
    if (m < 24) return { ...LEVELS.low, reason: `Only ${m} months of price history, which is too little to forecast with much confidence.` };
    if (m < 48) return { ...LEVELS.med, reason: `${m} months of price history, enough for a reasonable read.` };
    return { ...LEVELS.high, reason: `${m} months of price history, a long record to work from.` };
}
