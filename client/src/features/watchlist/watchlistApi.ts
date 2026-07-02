import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";

export type TrackedCard = {
    id: number;
    game: string;
    productId: number;
    addedAt: string;
}

export const watchlistApi = createApi({
    reducerPath: 'watchlistApi',
    baseQuery: baseQueryWithErrorHandling,
    tagTypes: ['Watchlist'],
    endpoints: (builder) => ({
        fetchWatchlist: builder.query<TrackedCard[], void>({
            query: () => 'watchlist',
            providesTags: ['Watchlist'],
        }),
        addToWatchlist: builder.mutation<void, { game: string; productId: number }>({
            query: (body) => ({ url: 'watchlist', method: 'POST', body }),
            invalidatesTags: ['Watchlist'],
        }),
        removeFromWatchlist: builder.mutation<void, { game: string; productId: number }>({
            query: ({ game, productId }) => ({ url: `watchlist/${game}/${productId}`, method: 'DELETE' }),
            invalidatesTags: ['Watchlist'],
        }),
    }),
});

export const {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
} = watchlistApi;
