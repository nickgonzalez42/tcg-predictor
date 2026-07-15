import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { Card } from "../../app/models/card";
import type { CardParams } from "../../app/models/cardParams";
import type { Pagination } from "../../app/models/pagination";
import { toApiParams } from "../../lib/util";

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
    // Wishlist detail (wishlist rows only).
    watchedAtPrice?: number;
    alertTargetPrice?: number;
}

// Brokerage-style portfolio rollup (GET portfolio/summary).
export type PortfolioPosition = {
    game: string;
    id: number;
    name?: string;
    pictureUrl?: string;
    pct: number;
    paid?: number;
    value?: number;
    plUsd?: number;
}

export type PortfolioSummary = {
    totalValue: number;
    copies: number;
    monthChangeUsd?: number | null;
    monthChangePct?: number | null;
    allTime?: { paid: number; value: number; plUsd: number; plPct: number } | null;
    allocation?: { label: string; value: number; pct: number }[];       // by game
    gradeAllocation?: { label: string; value: number; pct: number }[];  // by condition tier
    best?: PortfolioPosition | null;
    worst?: PortfolioPosition | null;
    series?: { date: string; value: number }[];
    // "Same $ in the market": daily S&P 500 what-if line (each copy's cost
    // basis invested in SPX on its add date), from account creation.
    benchmark?: { date: string; value: number }[];
    // Cumulative money-in (each copy's cost basis on its add date) — used to
    // strip contributions out of the chart's change figures.
    invested?: { date: string; value: number }[];
    accountCreated?: string;
}

// Editable per-copy fields sent to PATCH /watchlist/owned/{id}.
export type OwnedCopyEdit = {
    grade?: string | null;
    purchasePrice?: number | null;   // used only when autoPrice is false
    acquiredAt?: string | null;      // null resets to the copy's added date
    note?: string | null;
    autoPrice?: boolean;
}

export const watchlistApi = createApi({
    reducerPath: 'watchlistApi',
    baseQuery: baseQueryWithErrorHandling,
    // Scoped tags so a mutation only refetches the lists it touched:
    // 'Owned' (portfolio rows), 'Wishlist' (watch rows), 'Summary' (rollup).
    tagTypes: ['Owned', 'Wishlist', 'Summary'],
    endpoints: (builder) => ({
        // All tracked refs across both lists (used by TrackButton to know state).
        fetchWatchlist: builder.query<TrackedCard[], void>({
            query: () => 'watchlist',
            providesTags: ['Owned', 'Wishlist'],
        }),
        // A single list's cards, with catalog-style filtering/sorting/pagination.
        fetchTrackedCards: builder.query<{ items: Card[], pagination: Pagination }, { kind: TrackKind } & CardParams>({
            query: ({ kind, ...params }) => ({
                url: 'cards/tracked',
                params: toApiParams({ ...params, kind }),
            }),
            transformResponse: (items: Card[], meta) => {
                const header = meta?.response?.headers.get('Pagination');
                return { items, pagination: header ? JSON.parse(header) : null };
            },
            providesTags: (_res, _err, arg) => [arg.kind === 'owned' ? 'Owned' : 'Wishlist'],
        }),
        // For owned, each call adds a new copy (at the given condition); for wishlist
        // it's an idempotent toggle-on.
        addToWatchlist: builder.mutation<void, { game: string; productId: number; kind: TrackKind; grade?: string }>({
            query: (body) => ({ url: 'watchlist', method: 'POST', body }),
            invalidatesTags: (_res, _err, arg) =>
                arg.kind === 'owned' ? ['Owned', 'Summary'] : ['Wishlist'],
        }),
        removeFromWatchlist: builder.mutation<void, { game: string; productId: number; kind: TrackKind }>({
            query: ({ game, productId, kind }) => ({ url: `watchlist/${kind}/${game}/${productId}`, method: 'DELETE' }),
            invalidatesTags: (_res, _err, arg) =>
                arg.kind === 'owned' ? ['Owned', 'Summary'] : ['Wishlist'],
        }),
        // Set the number of copies owned at one condition (catalog quantity field).
        // Server only adds/removes blank copies; detailed ones are never auto-deleted.
        setOwnedQuantity: builder.mutation<{ quantity: number }, { game: string; productId: number; grade?: string; quantity: number }>({
            query: (body) => ({ url: 'watchlist/owned/quantity', method: 'PUT', body }),
            invalidatesTags: ['Owned', 'Summary'],
        }),
        // Owned copies are edited/removed individually by copy id (Owned page only).
        updateOwnedCopy: builder.mutation<void, { id: number } & OwnedCopyEdit>({
            query: ({ id, ...body }) => ({ url: `watchlist/owned/${id}`, method: 'PATCH', body }),
            invalidatesTags: ['Owned', 'Summary'],
        }),
        removeOwnedCopy: builder.mutation<void, { id: number }>({
            query: ({ id }) => ({ url: `watchlist/owned/${id}`, method: 'DELETE' }),
            invalidatesTags: ['Owned', 'Summary'],
        }),
        // Set (target) or clear (null) the price alert on a wishlist row.
        setWishlistAlert: builder.mutation<void, { game: string; productId: number; target: number | null }>({
            query: (body) => ({ url: 'watchlist/wishlist/alert', method: 'PUT', body }),
            invalidatesTags: ['Wishlist'],
        }),
        // Portfolio rollup: total value, monthly series, allocation, best/worst.
        fetchPortfolioSummary: builder.query<PortfolioSummary, string | void>({
            query: (game) => game && game !== 'all'
                ? `portfolio/summary?game=${game}` : 'portfolio/summary',
            providesTags: ['Summary'],
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
    useSetWishlistAlertMutation,
    useFetchPortfolioSummaryQuery,
} = watchlistApi;
