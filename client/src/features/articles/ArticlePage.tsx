import { useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { useFetchArticleQuery } from "./articlesApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { GAME_LABEL } from "../../lib/games";
import { longDate } from "../reports/reportKind";
import { useReportBodyEffects } from "../reports/useReportBodyEffects";
import { articleChipStyle, articleKindLabel } from "./articleKind";

// One per-game article (2026-11-06). The body is pipeline-generated HTML
// (text, card art, inline SVG charts) re-rendered through the strict report
// allowlist; the charts draw in on scroll and card links preview their art,
// exactly as on a weekly report.
export default function ArticlePage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: article, isLoading } = useFetchArticleQuery(slug!, { skip: !slug });
    usePageMeta(article?.title ?? "Article", article?.summary, {
        type: "article",
        canonical: slug ? `/articles/${slug}` : undefined,
        publishedAt: article?.publishedAt,
        section: article ? articleKindLabel(article.kind, false, article.focus) : undefined,
    });
    const bodyRef = useRef<HTMLDivElement>(null);
    useReportBodyEffects(bodyRef, article?.slug);

    if (isLoading) return <CardLoader />;
    if (!article) {
        return (
            <div className="reports full-span">
                <p className="est-note">
                    That article doesn't exist. <Link to="/articles">All articles</Link>
                </p>
            </div>
        );
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
            <div className="report__body" ref={bodyRef}
                dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(article.bodyHtml) }} />
            <p className="est-note article__foot">
                More on {game}: <Link to={`/articles?game=${article.game}`}>every {game} article</Link>,
                the <Link to={`/catalog?game=${article.game}`}>catalog</Link>, and the
                weekly <Link to="/reports">market and model reports</Link>.
            </p>
        </article>
    );
}
