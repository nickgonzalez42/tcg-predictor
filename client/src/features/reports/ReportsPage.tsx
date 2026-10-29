import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useFetchReportsQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { reportKind, reportKindLabel, longDate, type ReportKind } from "./reportKind";
import { gameColor, isGame } from "../../lib/games";

// Index of the weekly reports (newest first): every Friday a market report
// (what moved) and a model report (how the forecasts have done). The kind
// comes from the slug prefix, so no schema change was needed (2026-10-28).
// 2026-10-29: the per-article kind label became a filter at the top of the
// page, and each article carries its own descriptive headline + date instead.
// A chip takes the game's own colour, tinted for the fill and the ring so
// twelve games stay legible side by side on a dark panel. A tag that is not
// a game we price (never written today, but the column is free text) falls
// back to the muted default from gameColor().
const tagsOf = (tags?: string) =>
    (tags ? tags.split(',').map(t => t.trim()).filter(Boolean) : []);

const chipStyle = (tag: string) => {
    const color = isGame(tag) ? gameColor(tag) : 'var(--text-muted)';
    return {
        color,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        borderColor: `color-mix(in srgb, ${color} 55%, transparent)`,
    };
};

const TABS: { key: ReportKind | 'all'; label: string }[] = [
    { key: 'all', label: 'All reports' },
    { key: 'market', label: 'Market' },
    { key: 'model', label: 'Model' },
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
        const c = { all: reports?.length ?? 0, market: 0, model: 0 };
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
                                {/* Tag line: the date in plain type, then a
                                    chip per category. Each game carries its
                                    own colour (the one the portfolio donut
                                    and the charts already use for it) and
                                    the report type has its own. */}
                                <p className="reports__meta mono">
                                    <span className="reports__date">{longDate(r.publishedAt)}</span>
                                    {tagsOf(r.tags).map(tag => (
                                        <span key={tag} className="reports__chip"
                                            style={chipStyle(tag)}>{tag}</span>
                                    ))}
                                    <span className={`reports__chip reports__chip--${reportKind(r.slug)}`}>
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
