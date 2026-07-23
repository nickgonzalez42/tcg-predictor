import { Link } from "react-router-dom";
import { useFetchReportsQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";

// Index of the weekly market reports (newest first).
export default function ReportsPage() {
    usePageMeta("Market Reports",
        "Weekly trading card market reports: the week's biggest gainers and losers, per-game breadth, and where the price model sees movement next.");
    const { data: reports, isLoading } = useFetchReportsQuery();

    if (isLoading) return <CardLoader />;

    return (
        <div className="reports full-span">
            <h1 className="reports__title">Market Reports</h1>
            <p className="est-note reports__sub">
                A data-driven look at the week in card prices, published every Friday.
            </p>
            {!reports?.length ? (
                <p className="est-note">The first weekly report lands this Friday.</p>
            ) : (
                <ul className="reports__list">
                    {reports.map(r => (
                        <li key={r.slug} className="panel reports__item">
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
