import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useFetchArticlesQuery, type ArticleSummary } from "./articlesApi";
import AppPagination from "../../app/shared/components/AppPagination";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { GAMES, GAME_LABEL } from "../../lib/games";
import { longDate } from "../reports/reportKind";
import { articleChipStyle, articleKindLabel } from "./articleKind";

// Index of the per-game articles (2026-11-06). Two a week per game: a set
// report on the game's newest set and a game report on the whole game. One
// grid, newest first, with a game filter above it and page numbers under it
// (2026-11-07: a dozen a page; ?page= lives in the URL so a page can be
// shared and the back button returns to it). Each card names its kind, so
// there is no kind filter.
const PAGE = 12;

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
    const page = Math.max(1, parseInt(params.get('page') ?? '1', 10) || 1);
    const { data, isLoading } = useFetchArticlesQuery({
        game: game || undefined, limit: PAGE, offset: (page - 1) * PAGE,
    });

    // A new game filter starts over on page one and replaces the history
    // entry; a page flip pushes one, so Back returns to the previous page.
    const setGame = (next: string) => {
        const p = new URLSearchParams(params);
        if (next) p.set('game', next); else p.delete('game');
        p.delete('page');
        setParams(p, { replace: true });
    };
    const setPage = (next: number) => {
        const p = new URLSearchParams(params);
        if (next > 1) p.set('page', String(next)); else p.delete('page');
        setParams(p);
    };

    const items = data?.items ?? [];
    const total = data?.total ?? 0;
    const totalPages = Math.max(1, Math.ceil(total / PAGE));
    const pagination = total > PAGE
        ? { currentPage: page, totalPages, pageSize: PAGE, totalCount: total }
        : null;
    return (
        <div className="reports articles full-span">
            <h1 className="reports__title">Articles</h1>
            <p className="est-note reports__sub">
                Every game gets two articles a week: one on its newest set and one on the whole
                game. Two games a day, Monday to Saturday.
            </p>
            <div className="articles__filters" role="group" aria-label="Filter articles by game">
                <div className="articles__chips">
                    <button type="button" className={`articles__chip${!game ? ' articles__chip--on' : ''}`}
                        onClick={() => setGame('')}>All games</button>
                    {GAMES.map(g => (
                        <button type="button" key={g.value}
                            className={`articles__chip${game === g.value ? ' articles__chip--on' : ''}`}
                            onClick={() => setGame(g.value)}>{g.label}</button>
                    ))}
                </div>
            </div>
            {isLoading ? <CardLoader /> : items.length === 0 ? (
                <p className="est-note articles__end">
                    {page > 1 ? (
                        <>That page is past the end. <Link to={game ? `/articles?game=${game}` : '/articles'}>Back to the first page</Link>.</>
                    ) : game ? "No articles for that game yet." : "The first game articles land this week."}
                </p>
            ) : (
                <>
                    <ul className="reports__list articles__list">
                        {items.map(a => <ArticleCard key={a.slug} article={a} />)}
                    </ul>
                    <AppPagination metadata={pagination} onPageChange={setPage} />
                </>
            )}
        </div>
    );
}
