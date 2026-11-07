import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import { toApiParams } from "../../lib/util";

// Per-game articles (2026-11-06): two a week per game, one on its newest
// set and one on the whole game, written by the pipeline's game_articles.py
// and served read-only at /api/articles.
export type ArticleKind = 'set' | 'game';

export type ArticleSummary = {
    slug: string;
    kind: ArticleKind;
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
