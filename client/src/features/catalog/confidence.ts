// Forecast confidence, as reported by the model itself: each forecast carries
// the width of the model's own 10th–90th percentile scenario interval, and
// tight = high confidence. The months-of-history heuristic remains only as a
// fallback for rows scored before confidence existed.
const LEVELS: Record<string, { label: string; short: string; cls: string; reason: string }> = {
    high: {
        label: 'High confidence', short: 'HIGH', cls: 'conf--high',
        reason: "The model's own 80% scenario range for this forecast is tight, so its optimistic and pessimistic cases mostly agree.",
    },
    med: {
        label: 'Medium confidence', short: 'MED', cls: 'conf--med',
        reason: "The model's 80% scenario range is moderately wide, so treat the point forecast as a rough center for a spread of outcomes.",
    },
    low: {
        label: 'Low confidence', short: 'LOW', cls: 'conf--low',
        reason: "The model's 80% scenario range is wide, so the model is unsure and the low-to-high band matters more than the point forecast.",
    },
};

export function confidence(level?: string, months?: number) {
    if (level && LEVELS[level]) return LEVELS[level];
    // fallback heuristic (pre-confidence data): history depth
    const m = months ?? 0;
    if (m < 24) return { ...LEVELS.low, reason: `Only ${m} months of price history, too little for a reliable forecast.` };
    if (m < 48) return { ...LEVELS.med, reason: `${m} months of price history, a moderate track record.` };
    return { ...LEVELS.high, reason: `${m} months of price history, a long track record.` };
}
