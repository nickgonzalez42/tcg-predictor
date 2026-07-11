import { createSlice } from "@reduxjs/toolkit";
import type { CardParams, CatalogView } from "../../app/models/cardParams";
import { trendForSort } from "./sortOptions";

// Catalog opens on the strongest signal: projected % growth over a year.
export const DEFAULT_ORDER = 'chgPct12Desc';

const getInitialView = (): CatalogView =>
    localStorage.getItem('catalogView') === 'rows' ? 'rows' : 'cards';

const initialState: CardParams = {
    game: 'onepiece',
    pageNumber: 1,
    pageSize: 50,
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
            // Reset filters only — the cards/rows view choice is presentation, not a filter.
            return { ...initialState, game: state.game, view: state.view };
        },
        setParams(state, action) {
            return { ...state, ...action.payload };
        }
    }
});

export const { setGame, setOrderBy, setPageNumber, setPageSize, setRarities, setSearchTerm, setSets, setGrade, setMinPrice, setMaxPrice, setTrend, setView, resetParams, setParams } = catalogSlice.actions;
