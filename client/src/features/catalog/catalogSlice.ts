import { createSlice } from "@reduxjs/toolkit";
import type { CardParams } from "../../app/models/cardParams";

const initialState: CardParams = {
    game: 'onepiece',
    pageNumber: 1,
    pageSize: 50,
    sets: [],
    rarities: [],
    searchTerm: '',
    orderBy: 'name'
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
        resetParams(state) {
            return { ...initialState, game: state.game };
        },
        setParams(state, action) {
            return { ...state, ...action.payload };
        }
    }
});

export const { setGame, setOrderBy, setPageNumber, setPageSize, setRarities, setSearchTerm, setSets, resetParams, setParams } = catalogSlice.actions;
