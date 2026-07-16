const DONUT_COLORS: Record<string, string> = {
    // games
    'One Piece': '#3d7dca',
    'Pokémon': '#ffcb05',
    'Yu-Gi-Oh!': '#c678dd',
    'Magic': '#ff9e64',
    'Lorcana': '#4dd0e1',
    'Digimon': '#3fd98a',
    'Gundam': '#f06292',
    // condition tiers
    'Ungraded': '#3d7dca',
    'Grade 7': '#8b96ad',
    'Grade 8': '#4dd0e1',
    'Grade 9': '#ff9e64',
    'Grade 9.5': '#c678dd',
    'PSA 10': '#3fd98a',
    'BGS 10': '#ffcb05',
    'CGC 10': '#f06292',
    'SGC 10': '#e0e6f0',
};

// SVG donut of one allocation breakdown (by game, or by condition tier).
export default function AllocationDonut({ title, slices }: {
    title: string; slices: { label: string; value: number; pct: number }[];
}) {
    if (!slices.length) return null;

    const R = 40, C = 2 * Math.PI * R;
    let offset = 0;

    return (
        <div className="panel detail-panel">
            <span className="mono detail-panel__title">{title}</span>
            <div className="donut">
                <svg viewBox="0 0 100 100" className="donut__svg" aria-hidden="true">
                    {slices.map(s => {
                        const len = (s.pct / 100) * C;
                        const el = (
                            <circle
                                key={s.label}
                                cx="50" cy="50" r={R}
                                fill="none"
                                stroke={DONUT_COLORS[s.label] ?? '#8b96ad'}
                                strokeWidth="14"
                                strokeDasharray={`${len} ${C - len}`}
                                strokeDashoffset={-offset}
                                transform="rotate(-90 50 50)"
                            />
                        );
                        offset += len;
                        return el;
                    })}
                </svg>
                <ul className="donut__legend">
                    {slices.map(s => (
                        <li key={s.label}>
                            <span className="donut__dot" style={{ background: DONUT_COLORS[s.label] ?? '#8b96ad' }} />
                            {s.label} <span className="mono">{s.pct.toFixed(0)}%</span>
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    );
}
