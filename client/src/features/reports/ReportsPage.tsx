import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useFetchReportsQuery, type ReportSummary } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import {
    reportKind, reportKindLabel, reportChipStyle, longDate,
    REPORT_KINDS, REPORT_KIND_ORDER, type ReportKind,
} from "./reportKind";

// Index of the weekly reports. Each kind leads with its newest article as a
// full card; everything older sits in a dropdown under it as a horizontal
// rail (2026-10-30, user request), so the page stays one screen tall however
// many Fridays accumulate. There is no type filter: the page is already one
// section per type (2026-10-30, user request).

// One article as a card: its picture, headline, the opening of its lede, and
// the date beside its type. The whole card is the link.
function ReportCard({ report, compact }: { report: ReportSummary; compact?: boolean }) {
    const kind = reportKind(report.slug);
    const navigate = useNavigate();
    return (
        <li className={`panel reports__item${compact ? ' reports__item--compact' : ''}`}>
            {/* The same interactive picture the article leads with. Its cards
                and game bars are links of their own, so it sits OUTSIDE the
                card's link rather than nested inside it, which is invalid and
                would swallow them. Clicking anywhere else on the picture still
                opens the article; the headline below is the keyboard path. A
                report written before heroes existed simply has none. */}
            {report.hero && (
                <span className="reports__thumb"
                    onClick={e => {
                        if (!(e.target as Element).closest('a')) navigate(`/reports/${report.slug}`);
                    }}
                    dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(report.hero) }} />
            )}
            <Link to={`/reports/${report.slug}`} className="reports__card">
                <span className="reports__item-title">{report.title}</span>
                <span className="reports__summary">{report.summary}</span>
                <span className="reports__meta mono">
                    <span className="reports__date">{longDate(report.publishedAt)}</span>
                    <span className="reports__chip" style={reportChipStyle(kind)}>
                        {reportKindLabel(report.slug, true)}
                    </span>
                </span>
            </Link>
        </li>
    );
}

export default function ReportsPage() {
    usePageMeta("Reports",
        "Weekly trading card reports: what moved, where the price model sees movement next, and how its forecasts have actually done.",
        { canonical: "/reports" });
    const { data: reports, isLoading } = useFetchReportsQuery();

    // Newest first from the API, so the first of each kind is its latest.
    const byKind = useMemo(() => {
        const out = Object.fromEntries(REPORT_KIND_ORDER.map(k => [k, [] as ReportSummary[]])) as
            Record<ReportKind, ReportSummary[]>;
        for (const r of reports ?? []) out[reportKind(r.slug)].push(r);
        return out;
    }, [reports]);
    if (isLoading) return <CardLoader />;

    return (
        <div className="reports full-span">
            <h1 className="reports__title">Reports</h1>
            <p className="est-note reports__sub">
                Every Friday: a market report on what moved, and a model report on how our
                forecasts have been doing.
            </p>
            {!reports?.length ? (
                <p className="est-note">The first weekly report lands this Friday.</p>
            ) : (
                <>
                    {REPORT_KIND_ORDER.map(k => {
                        const [latest, ...older] = byKind[k];
                        if (!latest) return null;
                        return (
                            <section key={k} className="reports__section">
                                {/* No section heading: the card's own chip
                                    already says which kind it is (user
                                    2026-10-30). */}
                                <ul className="reports__list">
                                    <ReportCard report={latest} />
                                </ul>
                                {older.length > 0 && (
                                    <details className="reports__archive">
                                        <summary>
                                            Earlier {REPORT_KINDS[k].label.toLowerCase()} reports
                                            <span className="mono reports__filter-count">{older.length}</span>
                                        </summary>
                                        {/* A rail rather than a wall: the archive
                                            scrolls sideways and keeps the page short. */}
                                        <ul className="reports__rail">
                                            {older.map(r => <ReportCard key={r.slug} report={r} compact />)}
                                        </ul>
                                    </details>
                                )}
                            </section>
                        );
                    })}
                </>
            )}
        </div>
    );
}
