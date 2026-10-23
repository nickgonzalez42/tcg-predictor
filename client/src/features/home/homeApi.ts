import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";

// Numbers behind the home page's "at a glance" panels (GET home/stats):
// the model's public scorecard and where its 1-month calls lean, per game.
export type GameHit = { game: string; label: string; graded: number; bandHit: number };
export type GameLean = { game: string; label: string; up: number; flat: number; down: number };
export type HomeStats = {
    horizon: string;          // '1m'
    graded: number;           // forecasts graded at that horizon, all games
    bandHit: number;          // share that landed inside the model's 80% range
    gradedThrough?: string | null;
    byGame: GameHit[];        // sorted by bandHit desc
    lean: GameLean[];         // sorted by up-share desc
}

export const homeApi = createApi({
    reducerPath: 'homeApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        fetchHomeStats: builder.query<HomeStats, void>({
            query: () => 'home/stats',
        }),
    }),
});

export const { useFetchHomeStatsQuery } = homeApi;
