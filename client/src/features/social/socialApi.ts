import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { Pagination } from "../../app/models/pagination";

// ----- Profile -----
export type ProfileSettings = {
    handle: string | null;
    profilePublic: boolean;
    showPortfolio: boolean;
    showWatchlist: boolean;
    storefrontUrl: string | null;
    avatarGame: string | null;
    avatarProductId: number | null;
    avatarUrl: string | null;
    alertEmails?: boolean;   // opt-in: email when a card alert hits
};

export type PublicCardRow = {
    game: string; productId: number; name?: string; setName?: string;
    grade: string; quantity: number; price?: number; pictureUrl: string;
};

export type PublicProfile = {
    handle: string;
    joined: string;
    storefrontUrl?: string;
    avatarUrl?: string;
    watchlistCount?: number | null;   // null = list not shared
    portfolioCount?: number | null;
    totalValue?: number | null;
};

export type PublicCardsParams = {
    handle: string;
    list: 'portfolio' | 'watchlist';
    game: string;          // 'all' or a game key
    orderBy: string;
    pageNumber: number;
    pageSize: number;
};

// ----- Comments -----
export type CardComment = {
    id: number;
    parentId?: number | null;
    body?: string | null;        // null when author-deleted
    deleted: boolean;
    createdAt: string;
    author?: string | null;      // handle
    authorPublic: boolean;
    avatarUrl?: string | null;
    score: number;
    myVote: number;              // -1 | 0 | 1
    isMine: boolean;
};

export const socialApi = createApi({
    reducerPath: 'socialApi',
    baseQuery: baseQueryWithErrorHandling,
    tagTypes: ['Profile', 'Comments'],
    endpoints: (builder) => ({
        fetchMyProfile: builder.query<ProfileSettings, void>({
            query: () => 'profile/me',
            providesTags: ['Profile'],
        }),
        updateProfile: builder.mutation<ProfileSettings, Partial<ProfileSettings>>({
            query: (body) => ({ url: 'profile', method: 'PUT', body }),
            invalidatesTags: ['Profile'],
        }),
        fetchPublicProfile: builder.query<PublicProfile, string>({
            query: (handle) => `profile/${handle}`,
        }),
        fetchPublicCards: builder.query<{ items: PublicCardRow[], pagination: Pagination | null }, PublicCardsParams>({
            query: ({ handle, ...params }) => ({
                url: `profile/${handle}/cards`,
                params,
            }),
            transformResponse: (items: PublicCardRow[], meta) => {
                const header = meta?.response?.headers.get('Pagination');
                return { items, pagination: header ? JSON.parse(header) : null };
            },
        }),

        fetchComments: builder.query<CardComment[], { game: string; productId: number }>({
            query: ({ game, productId }) => `comments/${game}/${productId}`,
            providesTags: (_r, _e, arg) => [{ type: 'Comments', id: `${arg.game}-${arg.productId}` }],
        }),
        addComment: builder.mutation<{ id: number }, { game: string; productId: number; parentId?: number; body: string }>({
            query: (body) => ({ url: 'comments', method: 'POST', body }),
            invalidatesTags: (_r, _e, arg) => [{ type: 'Comments', id: `${arg.game}-${arg.productId}` }],
        }),
        deleteComment: builder.mutation<void, { id: number; game: string; productId: number }>({
            query: ({ id }) => ({ url: `comments/${id}`, method: 'DELETE' }),
            invalidatesTags: (_r, _e, arg) => [{ type: 'Comments', id: `${arg.game}-${arg.productId}` }],
        }),
        // Optimistic vote: patch the cached comment, roll back on failure.
        voteComment: builder.mutation<{ score: number; myVote: number }, { id: number; value: number; game: string; productId: number }>({
            query: ({ id, value }) => ({ url: `comments/${id}/vote`, method: 'PUT', body: { value } }),
            async onQueryStarted({ id, value, game, productId }, { dispatch, queryFulfilled }) {
                const patch = dispatch(socialApi.util.updateQueryData(
                    'fetchComments', { game, productId }, draft => {
                        const c = draft.find(x => x.id === id);
                        if (c) { c.score += value - c.myVote; c.myVote = value; }
                    }));
                try { await queryFulfilled; } catch { patch.undo(); }
            },
        }),
    }),
});

export const {
    useFetchMyProfileQuery,
    useUpdateProfileMutation,
    useFetchPublicProfileQuery,
    useFetchPublicCardsQuery,
    useFetchCommentsQuery,
    useAddCommentMutation,
    useDeleteCommentMutation,
    useVoteCommentMutation,
} = socialApi;
