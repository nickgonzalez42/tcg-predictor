// The visitor's game (2026-11-14): '' is every game, the default. Picked in
// the nav (GamePicker); the home page then shows that game's cards alone
// and the catalog opens on it. It lives in this browser only. A visitor
// whose very first page is an article about one game starts on that game
// (the static article pages set the same keys, see report_pages.py).
import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import { GAMES } from "./games";

const KEY = 'cs.game';
const SEEN = 'cs.seen';
const PICK = 'cs.pick';   // set once the visitor chooses in the picker themselves

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

// Where the visitor is coming from (2026-11-15). A game named by the link
// that brought them sets the site's game, so someone following a One Piece
// post lands on a One Piece site:
//   - a campaign tag on the URL (utm_campaign=onepiece-weekly) counts on
//     any arrival, first visit or not;
//   - the referring page (a subreddit's address, a game's forum) and the
//     landing page itself (a card, a set, the catalog opened on one game)
//     count on the first visit only.
// None of it ever overrides a game the visitor picked themselves.
const NAMES: [RegExp, string][] = [
    [/one[\s_+-]?piece|optcg/i, 'onepiece'], [/pok[eé]mon|pkmn|ptcg/i, 'pokemon'],
    [/yu[\s_+-]?gi[\s_+-]?oh|ygo/i, 'yugioh'], [/lorcana/i, 'lorcana'], [/digimon/i, 'digimon'],
    [/gundam/i, 'gundam'], [/star[\s_+-]?wars|swu/i, 'starwars'],
    [/flesh[\s_+-]?and[\s_+-]?blood|fabtcg/i, 'fleshandblood'], [/sorcery/i, 'sorcery'],
    [/riftbound/i, 'riftbound'], [/cyberpunk/i, 'cyberpunk'], [/magic|mtg/i, 'magic'],
];
const named = (text: string) => NAMES.find(([re]) => re.test(text))?.[1] ?? '';

function arrivalGame(first: boolean): string {
    try {
        const q = new URLSearchParams(window.location.search);
        const tags = ['utm_campaign', 'utm_content', 'utm_term', 'utm_source']
            .map(k => q.get(k) ?? '').join(' ');
        const tagged = named(tags);
        if (tagged || !first) return tagged;
        if (document.referrer) {
            const ref = new URL(document.referrer);
            if (ref.host !== window.location.host) {
                const from = named(decodeURIComponent(ref.host + ref.pathname));
                if (from) return from;
            }
        }
        const path = window.location.pathname.match(/^\/(?:catalog|sets)\/([a-z]+)(?:\/|$)/)?.[1];
        return known(path ?? q.get('game'));
    } catch {
        return '';
    }
}

{
    const game = read(PICK) ? '' : arrivalGame(firstVisitPath !== null);
    if (game && (firstVisitPath === null || read(KEY) === null)) write(KEY, game);
}

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
        // The visitor's own choice in the picker: it sticks, and nothing
        // automatic (an article, a campaign link) replaces it afterwards.
        pickSiteGame(state, action: PayloadAction<string>) {
            state.game = known(action.payload);
            write(KEY, state.game);
            write(PICK, '1');
        },
    },
});

export const { setSiteGame, pickSiteGame } = siteSlice.actions;

// Short marks for the picker's circle.
export const GAME_MARK: Record<string, string> = {
    cyberpunk: 'CYB', digimon: 'DIG', fleshandblood: 'FAB', gundam: 'GUN', lorcana: 'LOR', magic: 'MTG',
    onepiece: 'OP', pokemon: 'PKM', riftbound: 'RIF', sorcery: 'SOR', starwars: 'SWU', yugioh: 'YGO',
};
