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
        fetchCards: builder.query<{ items: Card[], pagination: Pagination }, CardParams>({
            query: (cardParams) => ({
                url: 'cards',
                params: toApiParams(cardParams)
            }),
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
        // Top movers by 12m forecast change, across both games (ticker + home tiles).
        fetchMovers: builder.query<Card[], number | void>({
            query: (count) => `cards/movers${count ? `?count=${count}` : ''}`
        })
    })
});

export const {
    useFetchCardDetailsQuery, useFetchCardsQuery, useFetchFiltersQuery,
    useFetchCardHistoryQuery, useFetchCardForecastQuery, useFetchCardForecastHistoryQuery,
    useFetchCardReasoningQuery, useFetchMoversQuery,
} = catalogApi;
