import { Link } from "react-router-dom";
import { useFetchReportsQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";

// Index of the weekly reports (newest first): every Friday a market report
// (what moved) and a model report (how the forecasts have done). The kind
// comes from the slug prefix, so no schema change was needed (2026-10-28).
const kindOf = (slug: string) => slug.startsWith('model-report') ? 'Model' : 'Market';
export default function ReportsPage() {
    usePageMeta("Reports",
        "Weekly trading card reports: what moved, where the price model sees movement next, and how its forecasts have actually done.");
    const { data: reports, isLoading } = useFetchReportsQuery();

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
                <ul className="reports__list">
                    {reports.map(r => (
                        <li key={r.slug} className="panel reports__item">
                            <span className={`mono reports__kind reports__kind--${kindOf(r.slug).toLowerCase()}`}>{kindOf(r.slug)}</span>
                            <Link to={`/reports/${r.slug}`} className="reports__item-title">
                                {r.title}
                            </Link>
                            <p className="reports__summary">{r.summary}</p>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
