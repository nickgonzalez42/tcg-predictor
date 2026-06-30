import { createApi } from "@reduxjs/toolkit/query/react";
import type { Card } from "../../app/models/card";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { CardParams } from "../../app/models/cardParams";
import { filterEmptyValues } from "../../lib/util";
import type { Pagination } from "../../app/models/pagination";

export const catalogApi = createApi({
    reducerPath: 'catalogApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        fetchCards: builder.query<{ items: Card[], pagination: Pagination }, CardParams>({
            query: (cardParams) => {
                return {
                    url: 'cards',
                    params: filterEmptyValues(cardParams)
                }
            },
            transformResponse: (items: Card[], meta) => {
                const paginationHeader = meta?.response?.headers.get('Pagination');
                const pagination = paginationHeader ? JSON.parse(paginationHeader) : null;
                return { items, pagination };
            }
        }),
        fetchCardDetails: builder.query<Card, { game: string, id: number }>({
            query: ({ game, id }) => `cards/${game}/${id}`
        }),
        fetchFilters: builder.query<{ sets: string[], rarities: string[] }, string>({
            query: (game) => `cards/filters?game=${game}`
        })
    })
});

export const { useFetchCardDetailsQuery, useFetchCardsQuery, useFetchFiltersQuery } = catalogApi;
