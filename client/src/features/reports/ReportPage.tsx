import { useRef } from "react";
import { useParams } from "react-router-dom";
import { useFetchReportQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import NotFound from "../../app/errors/NotFound";
import LoadFailed from "../../app/errors/LoadFailed";
import { isNotFound } from "../../app/api/baseApi";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { reportKindLabel, longDate } from "./reportKind";
import { useReportBodyEffects } from "./useReportBodyEffects";

// One weekly market report. The body is pipeline-generated HTML, re-rendered
// through the strict report allowlist before display.
export default function ReportPage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: report, isLoading, error, refetch } = useFetchReportQuery(slug!, { skip: !slug });
    // Each report is its own article: share card, canonical URL, publication
    // date and section all track the report rather than the site defaults
    // (2026-10-29). The prerendered copy at /reports/{slug} carries the same
    // tags for crawlers that do not run JavaScript.
    usePageMeta(report?.title ?? "Report", report?.summary, {
        type: "article",
        canonical: slug ? `/reports/${slug}` : undefined,
        publishedAt: report?.publishedAt,
        section: slug ? reportKindLabel(slug) : undefined,
    });
    const bodyRef = useRef<HTMLDivElement>(null);

    useReportBodyEffects(bodyRef, report?.slug);

    if (isLoading) return <CardLoader />;
    if (!report) {
        // A missing report is this page's own 404 (baseApi leaves 404s to the
        // page); any other failure says so instead of claiming it's gone.
        if (!error || isNotFound(error))
            return <NotFound message="We couldn't find that report." to="/reports" linkText="All reports" />;
        return <LoadFailed what="report" onRetry={refetch} />;
    }

    return (
        <article className="reports report full-span">
            {/* The kind used to head the article as an all-caps MARKET /
                MODEL chip (removed 2026-10-29, user request): the headline
                says what the article is about, and the type is in the page's
                article:section tag and the filter on /reports. */}
            {/* The interactive hero (report_thumbnail.render_svg); the JPEG
                twin is only what a link preview shows. */}
            {report.hero && (
                <div className="report__hero"
                    dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(report.hero) }} />
            )}
            <h1 className="reports__title">{report.title}</h1>
            <p className="reports__meta mono">
                Published {longDate(report.publishedAt)}
            </p>
            <div className="report__body" ref={bodyRef}
                dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(report.bodyHtml) }} />
        </article>
    );
}
