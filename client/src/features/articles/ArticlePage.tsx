import { useEffect, useMemo, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { useFetchArticleQuery } from "./articlesApi";
import CardLoader from "../../app/shared/components/CardLoader";
import NotFound from "../../app/errors/NotFound";
import LoadFailed from "../../app/errors/LoadFailed";
import { isNotFound } from "../../app/api/baseApi";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml, sanitizeReportSections } from "../../lib/sanitizeHtml";
import { GAME_LABEL } from "../../lib/games";
import { longDate } from "../reports/reportKind";
import { useReportBodyEffects } from "../reports/useReportBodyEffects";
import { articleChipStyle, articleKindLabel } from "./articleKind";
import { firstVisitPath, gameChosen, setSiteGame } from "../../lib/siteGame";
import { useAppDispatch } from "../../app/store/store";
import { ArticleBrowse, ArticleNext } from "./ArticleNext";

// One per-game article (2026-11-06). The body is pipeline-generated HTML
// (text, card art, inline SVG charts) re-rendered through the strict report
// allowlist; the charts draw in on scroll and card links preview their art,
// exactly as on a weekly report. The reader's next steps (2026-11-13,
// ArticleNext.tsx) close the opening section and follow the piece.
export default function ArticlePage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: article, isLoading, error, refetch } = useFetchArticleQuery(slug!, { skip: !slug });
    usePageMeta(article?.title ?? "Article", article?.summary, {
        type: "article",
        canonical: slug ? `/articles/${slug}` : undefined,
        publishedAt: article?.publishedAt,
        section: article ? articleKindLabel(article.kind, false, article.focus) : undefined,
    });
    // A visitor whose very first page is this article starts on its game
    // (2026-11-14), unless they have already settled one.
    const dispatch = useAppDispatch();
    useEffect(() => {
        if (article && firstVisitPath === `/articles/${article.slug}` && !gameChosen())
            dispatch(setSiteGame(article.game));
    }, [article, dispatch]);
    const bodyRef = useRef<HTMLDivElement>(null);
    useReportBodyEffects(bodyRef, article?.slug);
    const [opening, rest] = useMemo(
        () => sanitizeReportSections(article?.bodyHtml ?? ''), [article?.bodyHtml]);

    if (isLoading) return <CardLoader />;
    if (!article) {
        // A missing article is this page's own 404 (baseApi leaves 404s to the
        // page); any other failure says so instead of claiming it's gone.
        if (!error || isNotFound(error))
            return <NotFound message="We couldn't find that article." to="/articles" linkText="All articles" />;
        return <LoadFailed what="article" onRetry={refetch} />;
    }

    const game = GAME_LABEL[article.game] ?? article.game;
    return (
        <article className="reports report article full-span">
            {article.hero && (
                <div className="report__hero"
                    dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(article.hero) }} />
            )}
            <h1 className="reports__title">{article.title}</h1>
            <p className="reports__meta mono">
                <span className="reports__date">Published {longDate(article.publishedAt)}</span>
                <span className="reports__chip" style={articleChipStyle(article.kind, article.focus)}>
                    {articleKindLabel(article.kind, true, article.focus)}
                </span>
                <Link className="articles__game" to={`/articles?game=${article.game}`}>{game}</Link>
                {article.setName && <span className="articles__game">{article.setName}</span>}
            </p>
            {/* The body in two parts around the first next step: the opening
                section, then everything from the first section heading on. */}
            <div className="report__body" ref={bodyRef}>
                <div dangerouslySetInnerHTML={{ __html: opening }} />
                {rest && <ArticleBrowse article={article} />}
                {rest && <div dangerouslySetInnerHTML={{ __html: rest }} />}
            </div>
            <ArticleNext article={article} />
        </article>
    );
}
