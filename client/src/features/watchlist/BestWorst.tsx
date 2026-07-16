import { Link } from "react-router-dom";
import type { PortfolioSummary } from "./watchlistApi";
import ChangePill from "../../app/shared/components/ChangePill";
import { currencyFormat } from "../../lib/util";

// Best and worst positions by % gain, among copies with a recorded cost.
export default function BestWorst({ summary }: { summary: PortfolioSummary }) {
    const tiles = [
        { tag: 'Best position', p: summary.best },
        { tag: 'Worst position', p: summary.worst },
    ].filter(t => t.p);
    if (!tiles.length) return null;

    return (
        <div className="bw-tiles">
            {tiles.map(({ tag, p }) => (
                <Link key={tag} className="panel bw-tile" to={`/catalog/${p!.game}/${p!.id}`}>
                    <span className="mono detail-panel__title">{tag}</span>
                    <img className="bw-tile__thumb" src={p!.pictureUrl} alt="" loading="lazy" />
                    <span className="bw-tile__name">{p!.name}</span>
                    {p!.paid != null && p!.value != null && (
                        <span className="mono bw-tile__values">
                            {currencyFormat(p!.paid)} → {currencyFormat(p!.value)}
                        </span>
                    )}
                    <span className="bw-tile__pills">
                        <ChangePill value={p!.pct} />
                        {p!.plUsd != null && <ChangePill value={p!.plUsd} unit="usd" title="P/L vs paid" />}
                    </span>
                </Link>
            ))}
            <div className="est-note bw-tiles__note">
                Based on copies with a recorded purchase price.
            </div>
        </div>
    );
}
