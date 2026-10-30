import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useFetchReportsQuery, type ReportSummary } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import {
    reportKind, reportKindLabel, reportChipStyle, longDate,
    REPORT_KINDS, REPORT_KIND_ORDER, type ReportKind,
} from "./reportKind";

// Index of the weekly reports. Each kind leads with its newest article as a
// full card; everything older sits in a dropdown under it as a horizontal
// rail (2026-10-30, user request), so the page stays one screen tall however
// many Fridays accumulate.
const TABS: { key: ReportKind | 'all'; label: string }[] = [
    { key: 'all', label: 'All reports' },
    ...REPORT_KIND_ORDER.map(k => ({ key: k, label: REPORT_KINDS[k].label })),
];

// One article as a card: its picture, headline, the opening of its lede, and
// the date beside its type. The whole card is the link.
function ReportCard({ report, compact }: { report: ReportSummary; compact?: boolean }) {
    const kind = reportKind(report.slug);
    return (
        <li className={`panel reports__item${compact ? ' reports__item--compact' : ''}`}>
            <Link to={`/reports/${report.slug}`} className="reports__card">
                {/* Drawn beside the article by the pipeline and served from the
                    same path; a report without a picture simply drops it. */}
                <img className="reports__thumb" loading="lazy" alt=""
                    width={1200} height={630}
                    src={`/reports/${report.slug}.jpg`}
                    onError={e => { e.currentTarget.style.display = 'none'; }} />
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
    // The filter lives in the URL (?type=model), like the catalog's: a
    // filtered view can be shared and the back button steps through it.
    const [params, setParams] = useSearchParams();
    const asked = params.get('type');
    const kind: ReportKind | 'all' = asked === 'model' || asked === 'market' ? asked : 'all';
    const setKind = (next: ReportKind | 'all') =>
        setParams(next === 'all' ? {} : { type: next }, { replace: true });

    // Newest first from the API, so the first of each kind is its latest.
    const byKind = useMemo(() => {
        const out = Object.fromEntries(REPORT_KIND_ORDER.map(k => [k, [] as ReportSummary[]])) as
            Record<ReportKind, ReportSummary[]>;
        for (const r of reports ?? []) out[reportKind(r.slug)].push(r);
        return out;
    }, [reports]);
    const counts = useMemo(() => {
        const c = { all: reports?.length ?? 0 } as Record<ReportKind | 'all', number>;
        for (const k of REPORT_KIND_ORDER) c[k] = byKind[k].length;
        return c;
    }, [reports, byKind]);

    if (isLoading) return <CardLoader />;

    const shownKinds = REPORT_KIND_ORDER.filter(k => kind === 'all' || k === kind);

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
                    <div className="reports__filter" role="group" aria-label="Report type">
                        {TABS.map(t => (
                            <button key={t.key}
                                className={`btn btn--outline reports__filter-btn${kind === t.key ? ' btn--active' : ''}`}
                                onClick={() => setKind(t.key)}
                                aria-pressed={kind === t.key}
                                disabled={!counts[t.key]}
                            >
                                {t.label} <span className="mono reports__filter-count">{counts[t.key]}</span>
                            </button>
                        ))}
                    </div>
                    {shownKinds.map(k => {
                        const [latest, ...older] = byKind[k];
                        if (!latest) return null;
                        return (
                            <section key={k} className="reports__section">
                                <div className="band">
                                    <span className="band__title">Latest {REPORT_KINDS[k].label.toLowerCase()} report</span>
                                </div>
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
