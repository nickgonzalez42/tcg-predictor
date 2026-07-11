import { GAMES } from "../../../lib/games";

// Per-game chip row (Portfolio + Watchlist table headers).
type Props = {
    game: string
    onChange: (game: string) => void
}

export default function GameToggle({ game, onChange }: Props) {
    return (
        <div className="view-toggle" role="group" aria-label="Game">
            {GAMES.map(g => (
                <button key={g.value}
                    className={`btn btn--outline view-toggle__btn${game === g.value ? ' btn--active' : ''}`}
                    onClick={() => onChange(g.value)}>
                    {g.label}
                </button>
            ))}
        </div>
    );
}
