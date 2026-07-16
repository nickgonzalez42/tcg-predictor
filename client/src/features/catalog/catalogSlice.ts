import { createSlice } from "@reduxjs/toolkit";
import type { CardParams, CatalogView } from "../../app/models/cardParams";
import { trendForSort } from "./sortOptions";

// Catalog opens on the strongest signal: projected % growth over a year.
export const DEFAULT_ORDER = 'chgPct12Desc';
export const DEFAULT_PAGE_SIZE = 30;

const getInitialView = (): CatalogView =>
    localStorage.getItem('catalogView') === 'rows' ? 'rows' : 'cards';

const initialState: CardParams = {
    // Pre-decision placeholder; the real default is decided once per session:
    // URL param > the game with the most owned cards > Pokémon.
    game: 'pokemon',
    gameInitialized: false,
    pageNumber: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    sets: [],
    rarities: [],
    searchTerm: '',
    orderBy: DEFAULT_ORDER,
    grade: '',
    minPrice: '',
    maxPrice: '',
    trend: '1y',   // matches the default 1Y growth sort
    view: getInitialView()
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
            // Switching games invalidates the previous game's set/rarity filters.
            state.game = action.payload;
            state.sets = [];
            state.rarities = [];
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
        setTrend(state, action) {
            state.trend = action.payload;   // 1w | 1m | 6m | 1y
        },
        resetParams(state) {
            // Reset filters only — the cards/rows view choice is presentation,
            // not a filter, and the game default stays decided.
            return { ...initialState, game: state.game, view: state.view, gameInitialized: state.gameInitialized };
        },
        // Fresh navigation to /catalog with NO url params (a nav-link click, not
        // the back button): wipe filters AND the per-session game decision, so
        // the portfolio-majority default is re-picked. Back-button navigation
        // carries ?params and hydrates via setParams instead. View is
        // presentation state (persisted in localStorage), so it's preserved.
        resetToDefaults(state) {
            return { ...initialState, view: state.view };
        },
        setParams(state, action) {
            return { ...state, ...action.payload };
        }
    }
});

export const { setGame, setOrderBy, setPageNumber, setPageSize, setRarities, setSearchTerm, setSets, setGrade, setMinPrice, setMaxPrice, setTrend, setView, resetParams, resetToDefaults, setParams, initDefaultGame } = catalogSlice.actions;
