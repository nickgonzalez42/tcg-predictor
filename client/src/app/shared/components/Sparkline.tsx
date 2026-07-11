
// Tiny 64×20 trend line: green when trending up, red when down. No axes.
type Props = {
    points?: number[]
    width?: number
    height?: number
}

export default function Sparkline({ points, width = 64, height = 20 }: Props) {
    if (!points || points.length < 2) return <span className="sparkline sparkline--empty" />;

    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    const pad = 1.5; // keep the 2px stroke inside the viewBox
    const coords = points.map((p, i) => {
        const x = pad + (i / (points.length - 1)) * (width - pad * 2);
        const y = pad + (1 - (p - min) / span) * (height - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const up = points[points.length - 1] >= points[0];

    return (
        <svg
            className={`sparkline ${up ? 'sparkline--up' : 'sparkline--down'}`}
            width={width} height={height} viewBox={`0 0 ${width} ${height}`}
            aria-hidden="true" focusable="false"
        >
            <polyline points={coords.join(' ')} />
        </svg>
    );
}
