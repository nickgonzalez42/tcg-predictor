import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useFetchArticlesQuery, type ArticleKind, type ArticleSummary } from "./articlesApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { GAMES, GAME_LABEL } from "../../lib/games";
import { longDate } from "../reports/reportKind";
import { ARTICLE_KIND_ORDER, ARTICLE_KINDS, articleChipStyle, articleKindLabel } from "./articleKind";

// Index of the per-game articles (2026-11-06). Two a week per game: a set
// report on the game's newest set and a game report on the whole game. The
// page is one grid, newest first, with a game filter and a kind filter in
// one row above it; "Show more" extends the grid in place.
const PAGE = 24;

function ArticleCard({ article }: { article: ArticleSummary }) {
    const navigate = useNavigate();
    return (
        <li className="panel reports__item">
            {/* The hero's cards are links of their own, so the picture sits
                outside the card's link (nested anchors are invalid); clicking
                anywhere else on it still opens the article. */}
            {article.hero && (
                <span className="reports__thumb"
                    onClick={e => {
                        if (!(e.target as Element).closest('a')) navigate(`/articles/${article.slug}`);
                    }}
                    dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(article.hero) }} />
            )}
            <Link to={`/articles/${article.slug}`} className="reports__card">
                <span className="reports__item-title">{article.title}</span>
                <span className="reports__summary">{article.summary}</span>
                <span className="reports__meta mono">
                    <span className="reports__date">{longDate(article.publishedAt)}</span>
                    <span className="reports__chip" style={articleChipStyle(article.kind)}>
                        {articleKindLabel(article.kind, true)}
                    </span>
                    <span className="articles__game">{GAME_LABEL[article.game] ?? article.game}</span>
                </span>
            </Link>
        </li>
    );
}

export default function ArticlesPage() {
    usePageMeta("Articles",
        "Two articles per game every week: how its newest set is doing and how the whole game traded, with the cards, the charts and the model's outlook.",
        { canonical: "/articles" });
    const [params, setParams] = useSearchParams();
    const game = params.get('game') ?? '';
    const kindParam = params.get('kind');
    const kind = (kindParam === 'set' || kindParam === 'game') ? kindParam as ArticleKind : undefined;
    const [limit, setLimit] = useState(PAGE);
    const { data, isLoading, isFetching } = useFetchArticlesQuery({ game: game || undefined, kind, limit });

    const setFilter = (next: { game?: string; kind?: ArticleKind | '' }) => {
        const p = new URLSearchParams(params);
        if (next.game !== undefined) { if (next.game) p.set('game', next.game); else p.delete('game'); }
        if (next.kind !== undefined) { if (next.kind) p.set('kind', next.kind); else p.delete('kind'); }
        setParams(p, { replace: true });
        setLimit(PAGE);
    };

    const items = data?.items ?? [];
    const total = data?.total ?? 0;
    return (
        <div className="reports articles full-span">
            <h1 className="reports__title">Articles</h1>
            <p className="est-note reports__sub">
                Every game gets two articles a week: one on its newest set and one on the whole
                game. Two games a day, Monday to Saturday.
            </p>
            <div className="articles__filters" role="group" aria-label="Filter articles">
                <div className="articles__chips">
                    <button type="button" className={`articles__chip${!game ? ' articles__chip--on' : ''}`}
                        onClick={() => setFilter({ game: '' })}>All games</button>
                    {GAMES.map(g => (
                        <button type="button" key={g.value}
                            className={`articles__chip${game === g.value ? ' articles__chip--on' : ''}`}
                            onClick={() => setFilter({ game: g.value })}>{g.label}</button>
                    ))}
                </div>
                <div className="articles__chips">
                    <button type="button" className={`articles__chip${!kind ? ' articles__chip--on' : ''}`}
                        onClick={() => setFilter({ kind: '' })}>Both kinds</button>
                    {ARTICLE_KIND_ORDER.map(k => (
                        <button type="button" key={k}
                            className={`articles__chip${kind === k ? ' articles__chip--on' : ''}`}
                            style={kind === k ? articleChipStyle(k) : undefined}
                            onClick={() => setFilter({ kind: k })}>{ARTICLE_KINDS[k].long}s</button>
                    ))}
                </div>
            </div>
            {isLoading ? <CardLoader /> : items.length === 0 ? (
                <p className="est-note">
                    {game || kind ? "No articles match that filter yet." : "The first game articles land this week."}
                </p>
            ) : (
                <>
                    <ul className="reports__list articles__list">
                        {items.map(a => <ArticleCard key={a.slug} article={a} />)}
                    </ul>
                    {items.length < total && (
                        <button type="button" className="btn btn--outline articles__more"
                            disabled={isFetching} onClick={() => setLimit(l => l + PAGE)}>
                            {isFetching ? "Loading" : `Show more (${total - items.length} older)`}
                        </button>
                    )}
                </>
            )}
        </div>
    );
}
