import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import { toApiParams } from "../../lib/util";

// Per-game articles (2026-11-06): a set report and a game report per game,
// written by the pipeline's game_articles.py on its own schedule (each at
// least every two weeks, on a random night) and served read-only at
// /api/articles.
// 2026-11-14: 'type' follows one kind of card (One Piece Manga cards) through
// its whole history; its focus is the type's key and setName its name.
export type ArticleKind = 'set' | 'game' | 'type';
// 2026-11-07: the set slot takes turns between the newest set ('set') and the
// game's other recent releases ('recent'); older rows and game reports carry null.
export type ArticleFocus = 'set' | 'recent' | (string & {});

export type ArticleSummary = {
    slug: string;
    kind: ArticleKind;
    focus?: ArticleFocus | null;
    game: string;          // canonical key (pokemon, onepiece, ...)
    setName?: string | null;
    title: string;
    publishedAt: string;   // yyyy-MM-dd
    summary: string;
    /** The article's picture as inline SVG, sanitized before it is shown. */
    hero?: string;
};

export type Article = ArticleSummary & { bodyHtml: string };

export type ArticleList = { total: number; items: ArticleSummary[] };

export type ArticleQuery = { game?: string; kind?: ArticleKind; limit?: number; offset?: number };

export const articlesApi = createApi({
    reducerPath: 'articlesApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        fetchArticles: builder.query<ArticleList, ArticleQuery>({
            query: (params) => ({ url: 'articles', params: toApiParams(params) }),
        }),
        fetchArticle: builder.query<Article, string>({
            query: (slug) => `articles/${slug}`,
        }),
        // How many cards the catalog lists for a game, or one of its sets
        // (2026-11-13, an article's next steps): one card asked for, the
        // total read from the pagination header. Quiet when it fails: the
        // copy says "every card" instead of a number, and the shared error
        // handler, which can send a reader to the error page, never sees it.
        fetchCatalogCount: builder.query<number | null, { game: string; set?: string | null }>({
            queryFn: async ({ game, set }) => {
                try {
                    const q = new URLSearchParams({ game, orderBy: 'name', pageNumber: '1', pageSize: '1' });
                    if (set) q.set('sets', set);
                    const res = await fetch(`${import.meta.env.VITE_API_URL}/cards?${q}`, { credentials: 'include' });
                    const page = res.ok ? JSON.parse(res.headers.get('Pagination') ?? 'null') : null;
                    return { data: typeof page?.totalCount === 'number' ? page.totalCount : null };
                } catch {
                    return { data: null };
                }
            },
        }),
    }),
});

export const { useFetchArticlesQuery, useFetchArticleQuery, useFetchCatalogCountQuery } = articlesApi;
