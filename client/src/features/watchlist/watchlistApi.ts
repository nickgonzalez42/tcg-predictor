import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { Card } from "../../app/models/card";
import type { CardParams } from "../../app/models/cardParams";
import type { Pagination } from "../../app/models/pagination";
import { filterEmptyValues } from "../../lib/util";

export type TrackKind = 'owned' | 'wishlist';

export type TrackedCard = {
    id: number;
    game: string;
    productId: number;
    kind: TrackKind;
    addedAt: string;
    // Owned-copy detail (owned rows only; all optional).
    grade?: string;
    purchasePrice?: number;
    acquiredAt?: string;
    note?: string;
}

// Editable per-copy fields sent to PATCH /watchlist/owned/{id}.
export type OwnedCopyEdit = {
    grade?: string | null;
    purchasePrice?: number | null;
    acquiredAt?: string | null;
    note?: string | null;
}

export const watchlistApi = createApi({
    reducerPath: 'watchlistApi',
    baseQuery: baseQueryWithErrorHandling,
    tagTypes: ['Watchlist'],
    endpoints: (builder) => ({
        // All tracked refs across both lists (used by TrackButton to know state).
        fetchWatchlist: builder.query<TrackedCard[], void>({
            query: () => 'watchlist',
            providesTags: ['Watchlist'],
        }),
        // A single list's cards, with catalog-style filtering/sorting/pagination.
        fetchTrackedCards: builder.query<{ items: Card[], pagination: Pagination }, { kind: TrackKind } & CardParams>({
            query: ({ kind, ...params }) => ({
                url: 'cards/tracked',
                params: filterEmptyValues({ ...params, kind }),
            }),
            transformResponse: (items: Card[], meta) => {
                const header = meta?.response?.headers.get('Pagination');
                return { items, pagination: header ? JSON.parse(header) : null };
            },
            providesTags: ['Watchlist'],
        }),
        // For owned, each call adds a new copy (at the given condition); for wishlist
        // it's an idempotent toggle-on.
        addToWatchlist: builder.mutation<void, { game: string; productId: number; kind: TrackKind; grade?: string }>({
            query: (body) => ({ url: 'watchlist', method: 'POST', body }),
            invalidatesTags: ['Watchlist'],
        }),
        removeFromWatchlist: builder.mutation<void, { game: string; productId: number; kind: TrackKind }>({
            query: ({ game, productId, kind }) => ({ url: `watchlist/${kind}/${game}/${productId}`, method: 'DELETE' }),
            invalidatesTags: ['Watchlist'],
        }),
        // Set the number of copies owned at one condition (catalog quantity field).
        // Server only adds/removes blank copies; detailed ones are never auto-deleted.
        setOwnedQuantity: builder.mutation<{ quantity: number }, { game: string; productId: number; grade?: string; quantity: number }>({
            query: (body) => ({ url: 'watchlist/owned/quantity', method: 'PUT', body }),
            invalidatesTags: ['Watchlist'],
        }),
        // Owned copies are edited/removed individually by copy id (Owned page only).
        updateOwnedCopy: builder.mutation<void, { id: number } & OwnedCopyEdit>({
            query: ({ id, ...body }) => ({ url: `watchlist/owned/${id}`, method: 'PATCH', body }),
            invalidatesTags: ['Watchlist'],
        }),
        removeOwnedCopy: builder.mutation<void, { id: number }>({
            query: ({ id }) => ({ url: `watchlist/owned/${id}`, method: 'DELETE' }),
            invalidatesTags: ['Watchlist'],
        }),
    }),
});

export const {
    useFetchWatchlistQuery,
    useFetchTrackedCardsQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetOwnedQuantityMutation,
    useUpdateOwnedCopyMutation,
    useRemoveOwnedCopyMutation,
} = watchlistApi;
