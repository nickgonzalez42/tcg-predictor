import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { CardParams, CatalogView } from "../../app/models/cardParams";
import { PRERELEASE_SORTS, trendForSort } from "./sortOptions";
import { retargetSort, snapSort, snapTrend, type Windows } from "./windows";

// 6M $ growth: desc + the 6M trend view (2026-11-05, user decision). Six
// months is the horizon the catalog opens on: far enough out that the v4.5
// calibration still calls real growth (a 1M default opened on a wall of
// ~0% calls), near enough that the number means something to a reader.
// Dollars rather than percent (user decision 2026-11-05). Was 6M % growth
// from 2026-10-26, 6M $ growth earlier that day, 1Y $ growth from
// 2026-10-11, 1M $ growth before that. The trend chips follow the sort via
// trendForSort. A game with no 6M rows yet steps down to 1M (fitWindows)
// and comes back to this on its own once the rows exist.
export const DEFAULT_ORDER = 'chgUsd6Desc';
export const DEFAULT_PAGE_SIZE = 30;
// No default price floor (2026-11-02, user decision). A $1 floor stood here
// from 2026-10-26 because a PERCENT sort over a whole game put penny cards on
// top; the catalog now opens on a single set (the newest, see
// applyDefaultSets), where the ranking reads fine without one. A floor the
// visitor types is still honoured, and old links carrying minPrice=0 (the
// cleared-floor sentinel of that era) still parse.
// The trend window the opening sort implies; the chips follow the sort.
export const DEFAULT_TREND = trendForSort(DEFAULT_ORDER) ?? '6m';

const getInitialView = (): CatalogView =>
    localStorage.getItem('catalogView') === 'rows' ? 'rows' : 'cards';
const getInitialQuickAdd = (): boolean => localStorage.getItem('catalogQuickAdd') === '1';

const initialState: CardParams = {
    // Pre-decision placeholder; the real default is decided once per session:
    // URL param > the game with the most owned cards > Pokémon.
    game: 'pokemon',
    gameInitialized: false,
    pageNumber: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    // Sets start undecided: the newest set is applied once the game's facets
    // arrive (applyDefaultSets), unless a URL or the visitor settles it first.
    sets: [],
    rarities: [],
    // No confidence filter by default (2026-08-02): the catalog opens on the
    // whole priced set; the radio narrows to High/Medium/Low on demand.
    confidence: [],
    printings: [],
    searchTerm: '',
    orderBy: DEFAULT_ORDER,
    grade: '',
    minPrice: '',
    maxPrice: '',
    // Derived from the default sort so the tiles' window always matches the
    // opening sort (2026-08-22: a hardcoded '1y' here survived the switch to
    // a 1M default sort, so the catalog sorted by 1M while displaying 1Y).
    trend: DEFAULT_TREND,
    market: '',
    view: getInitialView(),
    quickAdd: getInitialQuickAdd()
}

// Filters that only mean something within one game. Cleared whenever the
// game changes, by a click (setGame) or by the session default
// (initDefaultGame); the newest set is then re-decided for the new game.
const forgetGameFilters = (state: CardParams) => {
    state.sets = [];
    state.setsDecided = undefined;   // the new game's newest set applies once its facets arrive
    state.rarities = [];
    state.printings = [];   // 2026-08-28: was silently filtering across game switches
    state.searchTerm = '';
    state.pageNumber = 1;
};

export const catalogSlice = createSlice({
    name: 'catalogSlice',
    initialState,
    reducers: {
        setPageNumber(state, action) {
            state.pageNumber = action.payload;
        },
        setPageSize(state, action) {
            state.pageSize = action.payload;
        },
        setOrderBy(state, action) {
            state.orderBy = action.payload;
            state.orderWish = undefined;   // a fresh pick replaces anything stepped down
            state.trendWish = undefined;
            state.pageNumber = 1;
            // A forecast sort also drives the tiles' trend window, so the
            // sparkline/movement period always matches what's being sorted.
            const trend = trendForSort(action.payload);
            if (trend) state.trend = trend;
        },
        setGame(state, action) {
            // Switching games invalidates the previous game's set/rarity/printing filters.
            state.game = action.payload;
            forgetGameFilters(state);
            state.gameInitialized = true;   // an explicit choice wins over defaults
            // Re-sync the trend window to the active sort, so the time-frame
            // chips always match the current filter after a game switch (a
            // young game with no 1Y then snaps 1y->6m in Catalog's effect).
            const trend = trendForSort(state.orderBy ?? '');
            if (trend) state.trend = trend;
        },
        // One-time session default: the game the user owns the most cards in
        // (Pokémon when signed out or the portfolio is empty). A no-op once any
        // decision — URL, user click, or this — has been made.
        initDefaultGame(state, action) {
            if (!state.gameInitialized && state.game !== action.payload) {
                // The placeholder game's facets may already have decided a
                // set (2026-11-03: a signed-in One Piece collector opened
                // /catalog, Pokémon's newest set was applied while the
                // watchlist loaded, then the game flipped and the list asked
                // One Piece for a Pokémon set: "no results"). Forget what was
                // decided for the placeholder, exactly as setGame does.
                state.game = action.payload;
                forgetGameFilters(state);
            }
            state.gameInitialized = true;
        },
        setSets(state, action) {
            state.sets = action.payload;
            state.setsDecided = 'user';
            state.pageNumber = 1;
        },
        // The opening set (2026-11-02, user: "automatically select the most
        // recent set for each game"): once the game's facets arrive, the
        // newest set the API names becomes the filter, unless the visitor has
        // already settled it (URL, a pick, a clear). Null = no default for
        // this mode, so the list stays open.
        applyDefaultSets(state, action: PayloadAction<{ game: string; set: string | null }>) {
            // The facets that named this set must be the current game's: a
            // decision made for another game would hide every card.
            if (state.setsDecided || action.payload.game !== state.game) return;
            state.sets = action.payload.set ? [action.payload.set] : [];
            state.setsDecided = 'default';
            state.pageNumber = 1;
        },
        setRarities(state, action) {
            state.rarities = action.payload;
            state.pageNumber = 1;
        },
        // Game-agnostic vocabulary (like confidence), so setGame leaves it alone.
        setPrintings(state, action) {
            state.printings = action.payload;
            state.pageNumber = 1;
        },
        // Game-agnostic (unlike sets/rarities), so setGame leaves it in place.
        setConfidence(state, action) {
            state.confidence = action.payload;
            state.pageNumber = 1;
        },
        setSearchTerm(state, action) {
            state.searchTerm = action.payload;
            state.pageNumber = 1;
            // A search is for a card, not a set: the newest-set default would
            // hide every other printing of it, so it steps aside. A set the
            // visitor chose themselves stays.
            if (action.payload && state.setsDecided === 'default') {
                state.sets = [];
                state.setsDecided = 'user';
            }
        },
        setGrade(state, action) {
            state.grade = action.payload;
            state.pageNumber = 1;
        },
        setMinPrice(state, action) {
            state.minPrice = action.payload;
            state.pageNumber = 1;
        },
        setMaxPrice(state, action) {
            state.maxPrice = action.payload;
            state.pageNumber = 1;
        },
        setView(state, action) {
            state.view = action.payload === 'rows' ? 'rows' : 'cards';
            localStorage.setItem('catalogView', state.view!);
        },
        // Quick-add mode (2026-10-10): presentation state like `view` —
        // remembered across sessions, never sent to the API.
        setQuickAdd(state, action) {
            state.quickAdd = !!action.payload;
            localStorage.setItem('catalogQuickAdd', state.quickAdd ? '1' : '0');
        },
        // The 1M / 6M / 1Y chips pick the window for everything on the page.
        // A forecast or past-change sort moves to the same window too
        // (2026-11-08, user: toggling the chips "doesn't change the forecast
        // shown"): the API shows the forecast the rows were RANKED on, so with
        // the sort left on 6M the chips only moved the sparkline and the
        // forecast column stayed 6M. Now 6M $ gain + the 1M chip = 1M $ gain,
        // and chip, ranking and shown forecast agree. Name/price sorts stay.
        setTrend(state, action) {
            state.trend = action.payload;   // 1w | 1m | 6m | 1y
            state.trendWish = undefined;
            const t = action.payload;
            if (t === '1m' || t === '6m' || t === '1y') {
                const retargeted = retargetSort(state.orderBy, t);
                if (retargeted !== state.orderBy) {
                    state.orderBy = retargeted;
                    state.orderWish = undefined;
                    state.pageNumber = 1;
                }
            }
        },
        // Fit the sort and trend to the windows the game can serve (facets
        // arrived, a game switch, or the model gained a horizon overnight),
        // remembering what the visitor asked for so it comes back the moment
        // the game can serve it: the 6M default on a game with 1M rows only
        // shows 1M today and 6M after the retrain, with no one touching it.
        fitWindows(state, action: PayloadAction<Windows>) {
            const w = action.payload;
            const wantOrder = state.orderWish ?? state.orderBy;
            const fitOrder = snapSort(wantOrder, w);
            state.orderWish = fitOrder === wantOrder ? undefined : wantOrder;
            state.orderBy = fitOrder;
            const wantTrend = state.trendWish ?? state.trend;
            const fitTrend = snapTrend(wantTrend, w);
            state.trendWish = fitTrend === wantTrend ? undefined : wantTrend;
            state.trend = fitTrend;
        },
        // Cards shown (2026-10-11): '' = with market price (default),
        // 'without' = predicted-price cards only, 'all' = both at once.
        // Game-agnostic. The modes have different set/rarity vocabularies,
        // so those clear on a switch; 'without' has only the estimate to
        // sort/filter on, so the sort snaps onto one it can serve and the
        // tier/confidence filters drop.
        setMarket(state, action) {
            const mode = action.payload === 'without' || action.payload === 'all'
                ? action.payload : '';
            if (mode === (state.market ?? '')) return;
            state.market = mode;
            state.sets = [];
            state.setsDecided = undefined;   // re-decided for the new mode's facets
            state.rarities = [];
            state.pageNumber = 1;
            if (mode === 'without') {
                if (!PRERELEASE_SORTS.includes(state.orderBy)) state.orderBy = 'priceDesc';
                state.orderWish = undefined;   // the estimate sorts serve every game
                state.trendWish = undefined;
                state.grade = '';
                state.confidence = [];
            }
        },
        resetParams(state) {
            // Reset filters only — the cards/rows view choice is presentation,
            // not a filter, and the game default stays decided.
            return { ...initialState, game: state.game, view: state.view, quickAdd: state.quickAdd, gameInitialized: state.gameInitialized };
        },
        // Fresh navigation to /catalog with NO url params (a nav-link click, not
        // the back button): wipe filters AND the per-session game decision, so
        // the portfolio-majority default is re-picked. Back-button navigation
        // carries ?params and hydrates via setParams instead. View is
        // presentation state (persisted in localStorage), so it's preserved.
        resetToDefaults(state) {
            return { ...initialState, view: state.view, quickAdd: state.quickAdd };
        },
        setParams(state, action) {
            return { ...state, ...action.payload };
        }
    }
});

export const { setGame, setOrderBy, setPageNumber, setPageSize, setRarities, setPrintings, setConfidence, setSearchTerm, setSets, applyDefaultSets, setGrade, setMinPrice, setMaxPrice, setTrend, fitWindows, setMarket, setView, setQuickAdd, resetParams, resetToDefaults, setParams, initDefaultGame } = catalogSlice.actions;
