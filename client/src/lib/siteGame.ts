// The visitor's game (2026-11-14): '' is every game, the default. Picked in
// the nav (GamePicker); the home page then shows that game's cards alone
// and the catalog opens on it. It lives in this browser only. A visitor
// whose very first page is an article about one game starts on that game
// (the static article pages set the same keys, see report_pages.py).
import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import { GAMES } from "./games";

const KEY = 'cs.game';
const SEEN = 'cs.seen';

function read(key: string): string | null {
    try { return localStorage.getItem(key); } catch { return null; }
}

function write(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch { /* private mode: the pick lasts the session */ }
}

const known = (g: string | null) => (g && GAMES.some(x => x.value === g) ? g : '');

// The path this browser's first ever visit landed on, or null when it has
// been here before. Read once, at load.
export const firstVisitPath: string | null = read(SEEN) ? null : window.location.pathname;
write(SEEN, '1');

// True once the visitor (or a first-visit article) has settled the game,
// "all games" included.
export const gameChosen = () => read(KEY) !== null;

export const siteSlice = createSlice({
    name: 'site',
    initialState: { game: known(read(KEY)) },
    reducers: {
        setSiteGame(state, action: PayloadAction<string>) {
            state.game = known(action.payload);
            write(KEY, state.game);
        },
    },
});

export const { setSiteGame } = siteSlice.actions;

// Short marks for the picker's circle.
export const GAME_MARK: Record<string, string> = {
    cyberpunk: 'CYB', digimon: 'DIG', fleshandblood: 'FAB', gundam: 'GUN', lorcana: 'LOR', magic: 'MTG',
    onepiece: 'OP', pokemon: 'PKM', riftbound: 'RIF', sorcery: 'SOR', starwars: 'SWU', yugioh: 'YGO',
};
