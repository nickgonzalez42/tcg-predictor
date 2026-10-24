import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";

// Numbers behind the home page's "at a glance" panels (GET home/stats):
// the model's public scorecard and where its 1-month calls lean, per game.
export type GameHit = { game: string; label: string; graded: number; bandHit: number };
export type GameLean = { game: string; label: string; up: number; flat: number; down: number };
// mae = typical size of the miss, bias = mean (predicted − realized); both
// are mean log-return errors (+bias = the model runs hot).
export type GameMiss = { game: string; label: string; graded: number; mae: number; bias: number };
export type HomeStats = {
    horizon: string;          // '1m'
    graded: number;           // forecasts graded at that horizon, all games
    bandHit: number;          // share that landed inside the model's 80% range
    gradedThrough?: string | null;
    byGame: GameHit[];        // sorted by bandHit desc
    lean: GameLean[];         // sorted by up-share desc
    mae: number;              // graded-weighted typical miss, all games
    bias: number;             // graded-weighted lean of the error
    miss: GameMiss[];         // sorted by mae asc (best first)
}

// Every model's public record (GET home/scorecard), for the About page.
export type ModelScore = {
    game: string; label: string; target: string; horizon: string;
    graded: number; bandHit: number; mae: number; bias: number; gradedThrough?: string | null;
};
export type WeekScore = {
    week: string; n: number; bandHit?: number | null; mae?: number | null; flatMae?: number | null;
    dirN?: number | null; dirHit?: number | null;
    n10?: number | null; bandHit10?: number | null; mae10?: number | null; flatMae10?: number | null;
};
export type MonthScore = {
    game: string; label: string; target: string; horizon: string; month: string;
    n: number; bandHit?: number | null; mae?: number | null; bias?: number | null;
};
export type Scorecard = { models: ModelScore[]; weekly: WeekScore[]; monthly: MonthScore[]; updatedAt?: string | null };

export const homeApi = createApi({
    reducerPath: 'homeApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        fetchHomeStats: builder.query<HomeStats, void>({
            query: () => 'home/stats',
        }),
        fetchScorecard: builder.query<Scorecard, void>({
            query: () => 'home/scorecard',
        }),
    }),
});

export const { useFetchHomeStatsQuery, useFetchScorecardQuery } = homeApi;
