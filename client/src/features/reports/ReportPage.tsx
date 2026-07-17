import { Link, useParams } from "react-router-dom";
import { useFetchReportQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { shortDate } from "../../lib/util";

// One weekly market report. The body is pipeline-generated HTML, re-rendered
// through the strict report allowlist before display.
export default function ReportPage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: report, isLoading } = useFetchReportQuery(slug!, { skip: !slug });
    usePageMeta(report?.title ?? "Market Report", report?.summary);

    if (isLoading) return <CardLoader />;
    if (!report) {
        return (
            <div className="reports full-span">
                <p className="est-note">
                    That report doesn't exist. <Link to="/reports">All reports</Link>
                </p>
            </div>
        );
    }

    return (
        <article className="reports report full-span">
            <nav className="est-note report__crumb">
                <Link to="/reports">Market Reports</Link> / {shortDate(report.publishedAt)}
            </nav>
            <h1 className="reports__title">{report.title}</h1>
            <div className="report__body"
                dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(report.bodyHtml) }} />
        </article>
    );
}
