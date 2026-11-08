import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import { toApiParams } from "../../lib/util";

// Per-game articles (2026-11-06): a set report and a game report per game,
// written by the pipeline's game_articles.py on its own schedule (each at
// least every two weeks, on a random night) and served read-only at
// /api/articles.
export type ArticleKind = 'set' | 'game';
// 2026-11-07: the set slot takes turns between the newest set ('set') and the
// game's other recent releases ('recent'); older rows and game reports carry null.
export type ArticleFocus = 'set' | 'recent';

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
    }),
});

export const { useFetchArticlesQuery, useFetchArticleQuery } = articlesApi;
