import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useFetchReportsQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import {
    reportKind, reportKindLabel, reportChipStyle, longDate,
    REPORT_KINDS, REPORT_KIND_ORDER, type ReportKind,
} from "./reportKind";

// Index of the weekly reports (newest first): every Friday a market report
// (what moved) and a model report (how the forecasts have done). The kind
// comes from the slug prefix, so no schema change was needed (2026-10-28).
// 2026-10-29: the per-article kind label became a filter at the top of the
// page, and each article carries its own descriptive headline + date instead.
// Filter row: everything, then one chip per kind, in the order the kinds
// are declared. A new kind shows up here on its own.
const TABS: { key: ReportKind | 'all'; label: string }[] = [
    { key: 'all', label: 'All reports' },
    ...REPORT_KIND_ORDER.map(k => ({ key: k, label: REPORT_KINDS[k].label })),
];

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

    const counts = useMemo(() => {
        const c = Object.fromEntries(REPORT_KIND_ORDER.map(k => [k, 0])) as
            Record<ReportKind | 'all', number>;
        c.all = reports?.length ?? 0;
        for (const r of reports ?? []) c[reportKind(r.slug)]++;
        return c;
    }, [reports]);
    const shown = useMemo(
        () => (reports ?? []).filter(r => kind === 'all' || reportKind(r.slug) === kind),
        [reports, kind]);

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
                    <ul className="reports__list">
                        {shown.map(r => (
                            <li key={r.slug} className="panel reports__item">
                                <Link to={`/reports/${r.slug}`} className="reports__item-title">
                                    {r.title}
                                </Link>
                                <p className="reports__summary">{r.summary}</p>
                                {/* Tag line: the date in plain type, then
                                    the kind of article in its own colour. */}
                                <p className="reports__meta mono">
                                    <span className="reports__date">{longDate(r.publishedAt)}</span>
                                    <span className="reports__chip"
                                        style={reportChipStyle(reportKind(r.slug))}>
                                        {reportKindLabel(r.slug, true)}
                                    </span>
                                </p>
                            </li>
                        ))}
                    </ul>
                </>
            )}
        </div>
    );
}
