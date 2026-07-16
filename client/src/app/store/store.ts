import { configureStore } from "@reduxjs/toolkit";
import { useDispatch, useSelector } from "react-redux";
import { catalogApi } from "../../features/catalog/catalogApi";
import { uiSlice } from "../layout/uiSlice";
import { catalogSlice } from "../../features/catalog/catalogSlice";
import { accountApi } from "../../features/account/accountApi";
import { watchlistApi } from "../../features/watchlist/watchlistApi";
import { socialApi } from "../../features/social/socialApi";
import { reportApi } from "../../features/report/reportApi";
import { ownedParamsSlice, wishlistParamsSlice } from "../../features/watchlist/trackedParamsSlice";

export const store = configureStore({
    reducer: {
        [catalogApi.reducerPath]: catalogApi.reducer,
        [accountApi.reducerPath]: accountApi.reducer,
        [watchlistApi.reducerPath]: watchlistApi.reducer,
        [socialApi.reducerPath]: socialApi.reducer,
        [reportApi.reducerPath]: reportApi.reducer,
        ui: uiSlice.reducer,
        catalog: catalogSlice.reducer,
        ownedParams: ownedParamsSlice.reducer,
        wishlistParams: wishlistParamsSlice.reducer
    },
    middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware().concat(
            catalogApi.middleware,
            socialApi.middleware,
            reportApi.middleware,
            accountApi.middleware,
            watchlistApi.middleware)
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch

export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();