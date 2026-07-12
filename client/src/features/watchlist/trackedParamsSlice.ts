import { createSlice } from "@reduxjs/toolkit";
import type { CardParams } from "../../app/models/cardParams";

// Owned and Wishlist pages each need their own independent filter/sort/pagination
// state, so we build the same slice twice via this factory. Default sort is ''
// (empty), which the API treats as "order added".
const initialState: CardParams = {
    game: 'onepiece',
    pageNumber: 1,
    pageSize: 50,
    sets: [],
    rarities: [],
    searchTerm: '',
    orderBy: '',
    grade: '',
    trend: '1m',
};

export function createTrackedParamsSlice(name: string) {
    return createSlice({
        name,
        initialState,
        reducers: {
            setPageNumber(state, action) {
                state.pageNumber = action.payload;
            },
            setOrderBy(state, action) {
                state.orderBy = action.payload;
                state.pageNumber = 1;
            },
            setGame(state, action) {
                state.game = action.payload;
                state.sets = [];
                state.rarities = [];
                state.searchTerm = '';
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
            setTrend(state, action) {
                state.trend = action.payload;
            },
            setSets(state, action) {
                state.sets = action.payload;
                state.pageNumber = 1;
            },
            setRarities(state, action) {
                state.rarities = action.payload;
                state.pageNumber = 1;
            },
            resetParams(state) {
                // Reset filters only — the game choice stays decided (as on catalog).
                return { ...initialState, game: state.game };
            },
        },
    });
}

export const ownedParamsSlice = createTrackedParamsSlice('ownedParams');
export const wishlistParamsSlice = createTrackedParamsSlice('wishlistParams');
