import { Link } from "react-router-dom";
import { useFetchMoversQuery } from "../../../features/catalog/catalogApi";
import { gameKey } from "../../../lib/util";

// Full-width scrolling strip of top movers under the navbar. Continuous ~30s
// marquee (CSS), pauses on hover; prefers-reduced-motion renders it static.
// Change figures are the model's 12-month forecast, labeled as such.
export default function MarketTicker() {
    const { data: movers } = useFetchMoversQuery(12);

    if (!movers?.length) return null;

    const chips = movers.map(m => {
        const pct = m.fcst12Pct ?? 0;
        const up = pct >= 0;
        return (
            <Link
                key={`${m.game}-${m.id}`}
                className={`tkc ${up ? 'tkc--up' : 'tkc--down'}`}
                to={`/catalog/${gameKey(m.game)}/${m.id}`}
            >
                {m.name} {up ? '▲' : '▼'} {up ? '+' : '−'}{Math.abs(pct).toFixed(1)}%
            </Link>
        );
    });

    return (
        <div className="ticker" aria-label="Top movers — 12 month model forecast">
            <div className="ticker__track">
                {/* content twice for a seamless -50% loop */}
                <div className="ticker__group">{chips}<span className="mono ticker__tag">— 12M FORECAST —</span></div>
                <div className="ticker__group" aria-hidden="true">{chips}<span className="mono ticker__tag">— 12M FORECAST —</span></div>
            </div>
        </div>
    );
}
