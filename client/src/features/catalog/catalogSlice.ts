import { createSlice } from "@reduxjs/toolkit";
import type { CardParams, CatalogView } from "../../app/models/cardParams";
import { PRERELEASE_SORTS, trendForSort } from "./sortOptions";

// 6M % growth: desc + the 6M trend view (2026-10-26, user decision). Six
// months is the horizon the catalog opens on: far enough out that the v4.5
// calibration still calls real growth (a 1M default opened on a wall of
// ~0% calls), near enough that the number means something to a reader.
// Percent rather than dollars, so a cheap card with a big call can lead
// instead of only the expensive ones. Was 6M $ growth earlier the same
// day, 1Y $ growth from 2026-10-11, 1M $ growth before that. The trend
// chips follow the sort via trendForSort.
export const DEFAULT_ORDER = 'chgPct6Desc';
export const DEFAULT_PAGE_SIZE = 30;
// Default price floor (2026-10-26). Sorting by PERCENT puts penny cards on
// top — a $0.01 card called at $0.05 is +400%, and the opening screen filled
// with them (one $0.15 card read +31,453%). A $1 floor is enough to clear
// that and still keeps genuinely cheap cards. It shows in the Min $ box, so
// it is visible and a visitor can clear it; clearing writes minPrice=0 to
// the URL, the sentinel the parser already understands.
export const DEFAULT_MIN_PRICE = '1';
// The trend window the opening sort implies; the chips follow the sort.
export const DEFAULT_TREND = trendForSort('chgPct6Desc') ?? '6m';

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
    sets: [],
    rarities: [],
    // No confidence filter by default (2026-08-02): the catalog opens on the
    // whole priced set; the radio narrows to High/Medium/Low on demand.
    confidence: [],
    printings: [],
    searchTerm: '',
    orderBy: DEFAULT_ORDER,
    grade: '',
    minPrice: DEFAULT_MIN_PRICE,
    maxPrice: '',
    // Derived from the default sort so the tiles' window always matches the
    // opening sort (2026-08-22: a hardcoded '1y' here survived the switch to
    // a 1M default sort, so the catalog sorted by 1M while displaying 1Y).
    trend: DEFAULT_TREND,
    market: '',
    view: getInitialView(),
    quickAdd: getInitialQuickAdd()
}

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
            state.pageNumber = 1;
            // A forecast sort also drives the tiles' trend window, so the
            // sparkline/movement period always matches what's being sorted.
            const trend = trendForSort(action.payload);
            if (trend) state.trend = trend;
        },
        setGame(state, action) {
            // Switching games invalidates the previous game's set/rarity/printing filters.
            state.game = action.payload;
            state.sets = [];
            state.rarities = [];
            state.printings = [];   // 2026-08-28: was silently filtering across game switches
            state.searchTerm = '';
            state.pageNumber = 1;
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
            if (!state.gameInitialized) state.game = action.payload;
            state.gameInitialized = true;
        },
        setSets(state, action) {
            state.sets = action.payload;
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
        setTrend(state, action) {
            state.trend = action.payload;   // 1w | 1m | 6m | 1y
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
            state.rarities = [];
            state.pageNumber = 1;
            if (mode === 'without') {
                if (!PRERELEASE_SORTS.includes(state.orderBy)) state.orderBy = 'priceDesc';
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

export const { setGame, setOrderBy, setPageNumber, setPageSize, setRarities, setPrintings, setConfidence, setSearchTerm, setSets, setGrade, setMinPrice, setMaxPrice, setTrend, setMarket, setView, setQuickAdd, resetParams, resetToDefaults, setParams, initDefaultGame } = catalogSlice.actions;
