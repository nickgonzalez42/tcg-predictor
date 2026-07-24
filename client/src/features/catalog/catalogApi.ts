import { createApi } from "@reduxjs/toolkit/query/react";
import type { Card, Forecast, PastForecast } from "../../app/models/card";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { CardParams } from "../../app/models/cardParams";
import { toApiParams } from "../../lib/util";
import type { Pagination } from "../../app/models/pagination";

export const catalogApi = createApi({
    reducerPath: 'catalogApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        fetchCards: builder.query<{ items: Card[], pagination: Pagination | null }, CardParams>({
            query: (cardParams) => ({
                url: 'cards',
                params: toApiParams(cardParams)
            }),
            // Key the cache on what actually reaches the API: client-only
            // presentation state (view, gameInitialized) must not refetch an
            // identical list when it changes.
            serializeQueryArgs: ({ queryArgs, endpointName }) =>
                `${endpointName}(${JSON.stringify(toApiParams(queryArgs))})`,
            transformResponse: (items: Card[], meta) => {
                const paginationHeader = meta?.response?.headers.get('Pagination');
                const pagination = paginationHeader ? JSON.parse(paginationHeader) : null;
                return { items, pagination };
            }
        }),
        fetchCardDetails: builder.query<Card, { game: string, id: number }>({
            query: ({ game, id }) => `cards/${game}/${id}`
        }),
        fetchFilters: builder.query<{ sets: string[], rarities: string[], hasYear?: boolean }, string>({
            query: (game) => `cards/filters?game=${game}`
        }),
        fetchCardHistory: builder.query<
            { game: string, productId: number, series: Record<string, { date: string, price: number, source?: string }[]> },
            { game: string, id: number }
        >({
            query: ({ game, id }) => `cards/${game}/${id}/history`
        }),
        fetchCardForecast: builder.query<
            { game: string, productId: number, forecasts: Forecast[] },
            { game: string, id: number }
        >({
            query: ({ game, id }) => `cards/${game}/${id}/forecast`
        }),
        // Archived forecasts whose horizon has elapsed — "what the model said
        // back then", drawn on the chart for accuracy review.
        fetchCardForecastHistory: builder.query<
            { game: string, productId: number, forecasts: PastForecast[] },
            { game: string, id: number }
        >({
            query: ({ game, id }) => `cards/${game}/${id}/forecast-history`
        }),
        fetchCardReasoning: builder.query<
            { game: string, productId: number, prose: string | null },
            { game: string, id: number }
        >({
            query: ({ game, id }) => `cards/${game}/${id}/reasoning`
        }),
        // Top movers by forecast change across all games (ticker + home tiles).
        // horizon: 1m | 6m | 12m (default 12m, with the young-game 6m fallback).
        // trend: displayed history window (sparkline + PAST pill), default 1y.
        // perGame (mix only): guarantee every game that many cards (the hero).
        fetchMovers: builder.query<Card[], { count?: number; horizon?: string; trend?: string; perGame?: number } | void>({
            query: (args) => {
                const p = new URLSearchParams();
                if (args?.count) p.set('count', String(args.count));
                if (args?.horizon) p.set('horizon', args.horizon);
                if (args?.trend) p.set('trend', args.trend);
                if (args?.perGame) p.set('perGame', String(args.perGame));
                const qs = p.toString();
                return `cards/movers${qs ? `?${qs}` : ''}`;
            }
        })
    })
});

export const {
    useFetchCardDetailsQuery, useFetchCardsQuery, useFetchFiltersQuery,
    useFetchCardHistoryQuery, useFetchCardForecastQuery, useFetchCardForecastHistoryQuery,
    useFetchCardReasoningQuery, useFetchMoversQuery,
} = catalogApi;
