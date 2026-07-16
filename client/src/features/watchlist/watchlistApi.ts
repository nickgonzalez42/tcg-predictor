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

// --- Bulk CSV import (POST /watchlist/owned/import) ---
// A parsed CSV row: identify a card by productId or by name, with optional
// grade, quantity, price paid, and acquired date.
export type ImportRow = {
    game: string;
    productId?: number;
    name?: string;
    grade?: string;
    quantity: number;
    purchasePrice?: number;
    acquiredAt?: string;   // yyyy-MM-dd
}

// A candidate card returned when a name matched more than one card.
export type ImportCandidate = {
    game: string;
    productId: number;
    name?: string;
    setName?: string;
    rarity?: string;
    price?: number;
    imageUrl?: string;
}

export type ImportRowResult = {
    index: number;
    status: 'imported' | 'ambiguous' | 'error';
    added: number;
    message?: string;
    candidates?: ImportCandidate[];
}

export type ImportResult = { added: number; rows: ImportRowResult[] };

// --- Card alerts (GET/POST /alerts, DELETE /alerts/{id}) ---
// Several per card: on the current price, a forecast price, or a forecast %
// change, scoped to a condition tier (+ horizon for forecast kinds). The
// server evaluates `current`/`hit` when listing.
export type AlertKind = 'price' | 'fcst_price' | 'fcst_pct';

export type CardAlert = {
    id: number;
    game: string;
    productId: number;
    grade?: string | null;      // null = ungraded
    kind: AlertKind;
    horizon?: string | null;    // 1w | 1m | 6m | 12m (forecast kinds)
    direction: 'above' | 'below';
    target: number;             // $ for price kinds, % for fcst_pct
    current?: number | null;    // live value the alert is judged against
    hit: boolean;
    createdAt: string;
    // Card context for the notifications page rows.
    name?: string | null;
    setName?: string | null;
    pictureUrl?: string | null;
}

export type NewAlert = {
    game: string;
    productId: number;
    grade?: string;
    kind: AlertKind;
    horizon?: string;
    direction: 'above' | 'below';
    target: number;
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
    tagTypes: ['Owned', 'Wishlist', 'Summary', 'Alerts'],
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
        // Bulk import owned copies parsed from a CSV. Rows identified by name that
        // match several cards come back as 'ambiguous' with candidates to pick.
        importOwned: builder.mutation<ImportResult, { rows: ImportRow[] }>({
            query: (body) => ({ url: 'watchlist/owned/import', method: 'POST', body }),
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
        // All of the user's card alerts, evaluated (current value + hit) server-side.
        fetchAlerts: builder.query<CardAlert[], void>({
            query: () => 'alerts',
            providesTags: ['Alerts'],
        }),
        addAlert: builder.mutation<{ id: number }, NewAlert>({
            query: (body) => ({ url: 'alerts', method: 'POST', body }),
            invalidatesTags: ['Alerts'],
        }),
        deleteAlert: builder.mutation<void, { id: number }>({
            query: ({ id }) => ({ url: `alerts/${id}`, method: 'DELETE' }),
            invalidatesTags: ['Alerts'],
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
    useImportOwnedMutation,
    useUpdateOwnedCopyMutation,
    useRemoveOwnedCopyMutation,
    useFetchAlertsQuery,
    useAddAlertMutation,
    useDeleteAlertMutation,
    useFetchPortfolioSummaryQuery,
} = watchlistApi;
