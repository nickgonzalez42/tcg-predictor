import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { GAMES, GAME_LABEL, gameColor } from "../../lib/games";
import { GAME_MARK, gameImage, pickSiteGame } from "../../lib/siteGame";
import { setGame } from "../../features/catalog/catalogSlice";
import { useAppDispatch, useAppSelector } from "../store/store";

// The game picker (2026-11-14): a round mark under the logo on desktop, at
// the top right of the bar on tablet and phone. Hovering it (or tapping it)
// lists the games; picking one makes the home page show that game's cards
// alone and the catalog open on it. "All games" is the default.
export default function GamePicker() {
    const game = useAppSelector(state => state.site.game);
    const dispatch = useAppDispatch();
    const ref = useRef<HTMLDivElement>(null);
    const { pathname } = useLocation();
    // Open belongs to the page it was opened on: navigating closes the menu
    // without an effect having to reset it.
    const [openOn, setOpenOn] = useState<string | null>(null);
    const open = openOn === pathname;

    useEffect(() => {
        if (!open) return;
        const onOutside = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpenOn(null);
        };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenOn(null); };
        document.addEventListener('click', onOutside);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('click', onOutside);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const pick = (next: string) => {
        dispatch(pickSiteGame(next));
        // The catalog follows a real game; "all games" leaves it where it is.
        if (next) dispatch(setGame(next));
        setOpenOn(null);
        (document.activeElement as HTMLElement | null)?.blur();
    };

    const label = game ? GAME_LABEL[game] : 'All games';
    return (
        <div ref={ref} className={`gamepick${open ? ' gamepick--open' : ''}`}>
            <button type="button" className="gamepick__mark"
                style={game ? { borderColor: gameColor(game), color: gameColor(game) } : undefined}
                aria-haspopup="listbox" aria-expanded={open}
                aria-label={`Game: ${label}. Choose a game`}
                onClick={() => setOpenOn(open ? null : pathname)}>
                {!game ? <span className="gamepick__img gamepick__img--all" />
                    : gameImage(game) ? <img className="gamepick__img" src={gameImage(game)} alt="" width={26} height={26} />
                    : GAME_MARK[game]}
            </button>
            <ul className="gamepick__menu" role="listbox" aria-label="Choose a game">
                <li>
                    <button type="button" role="option" aria-selected={!game}
                        className={`gamepick__option${!game ? ' gamepick__option--on' : ''}`}
                        onClick={() => pick('')}>
                        <span className="gamepick__dot" />All games
                    </button>
                </li>
                {GAMES.map(g => (
                    <li key={g.value}>
                        <button type="button" role="option" aria-selected={game === g.value}
                            className={`gamepick__option${game === g.value ? ' gamepick__option--on' : ''}`}
                            onClick={() => pick(g.value)}>
                            <span className="gamepick__dot" style={{ background: gameColor(g.value) }} />
                            {g.label}
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    );
}
