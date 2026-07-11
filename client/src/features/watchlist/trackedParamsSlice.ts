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
        },
    });
}

export const ownedParamsSlice = createTrackedParamsSlice('ownedParams');
export const wishlistParamsSlice = createTrackedParamsSlice('wishlistParams');
