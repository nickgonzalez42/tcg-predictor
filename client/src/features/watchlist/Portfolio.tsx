import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { useFetchTrackedCardsQuery, useFetchPortfolioSummaryQuery, useFetchWatchlistQuery } from "./watchlistApi";
import { ownedParamsSlice } from "./trackedParamsSlice";
import { trackedSortGroups } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import SortTh from "../../app/shared/components/SortTh";
import TrackedFilters from "./TrackedFilters";
import { useFetchFiltersQuery } from "../catalog/catalogApi";
import ChangePill from "../../app/shared/components/ChangePill";
import { currencyFormat } from "../../lib/util";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import ImportModal from "./ImportModal";
import ValueChart from "./ValueChart";
import AllocationDonut from "./AllocationDonut";
import BestWorst from "./BestWorst";
import PositionRow from "./PositionRow";
import PaidHelpModal from "./PaidHelpModal";
import SoldTable from "./SoldTable";
import GradingModal from "./GradingModal";
import CollectionAnalysisModal from "./CollectionAnalysisModal";
import ToolsMenu from "./ToolsMenu";
import OutlookModal from "./OutlookModal";

export default function Portfolio() {
    usePageMeta("Portfolio");
    const [showPaidHelp, setShowPaidHelp] = useState(false);
    const [showImport, setShowImport] = useState(false);
    // Tools menu (2026-10-25): "Recommend grading" (which Near Mint cards
    // gain most from PSA grading) and "Collection analysis" (infographics).
    const [showGrading, setShowGrading] = useState(false);
    const [showAnalysis, setShowAnalysis] = useState(false);
    // "Value outlook" (2026-11-05): what the collection could be worth at
    // 1 month, 6 months and 1 year by the model's calls.
    const [showOutlook, setShowOutlook] = useState(false);
    // Positions (what you hold) vs the Sold track (what you've sold, with
    // realized P/L) — two tabs over the same table area (2026-10-24).
    // "Listed" (2026-11-12) is the positions table narrowed to the copies
    // marked as listed for sale; the tab appears once something is marked.
    const [view, setView] = useState<'positions' | 'listed' | 'sold'>('positions');
    const { setPageNumber, setOrderBy } = ownedParamsSlice.actions;
    const params = useAppSelector(state => state.ownedParams);
    const dispatch = useAppDispatch();

    // Pack pulls can be hidden from the whole review — headline value, chart,
    // allocation, best/worst AND the positions list below (2026-10-02). They
    // never count in the money lines either way.
    const [includePacks, setIncludePacks] = useState(true);
    const { data, currentData, isLoading, isError, refetch } = useFetchTrackedCardsQuery(
        { kind: 'owned', includePacks, listed: view === 'listed', ...params });
    const { data: summary } = useFetchPortfolioSummaryQuery({ includePacks });

    // The game filter defaults to wherever the user most recently added a
    // card, and keeps following that until they pick a game themselves
    // (the catalog defaults the same way, by portfolio majority).
    const { data: tracked } = useFetchWatchlistQuery();
    useEffect(() => {
        if (!tracked || params.gameInitialized) return;
        let latest: { game: string; addedAt: string } | undefined;
        for (const t of tracked)
            if (t.kind === 'owned' && (!latest || t.addedAt > latest.addedAt)) latest = t;
        if (latest) dispatch(ownedParamsSlice.actions.syncDefaultGame(latest.game));
    }, [tracked, params.gameInitialized, dispatch]);
    // Young games (digimon/gundam) have no 12m horizon yet — the forecast
    // column falls back to their 6m numbers and relabels itself.
    const { data: filtersData } = useFetchFiltersQuery({ game: params.game });
    const hasYear = filtersData?.hasYear ?? true;

    // A truly empty portfolio (no copies at all, filters aside) skips the page
    // furniture — chart hero, allocation rail, filters — and shows just the
    // empty state with the Import onramp.
    const empty = summary != null && summary.copies === 0;
    const soldCount = summary?.realized?.count ?? 0;
    const listedCount = tracked?.filter(t => t.kind === 'owned' && t.listedAt).length ?? 0;
    // Both tabs page the same list, so a switch starts from the first page.
    const showView = (v: typeof view) => { setView(v); dispatch(setPageNumber(1)); };

    // Download the positions as a CSV that round-trips through Import (cookie
    // auth rides the fetch; blob keeps it a same-page download).
    const exportCsv = async () => {
        const res = await fetch(`${import.meta.env.VITE_API_URL}/watchlist/owned/export`,
            { credentials: 'include' });
        if (!res.ok) return;
        const url = URL.createObjectURL(await res.blob());
        const a = document.createElement('a');
        a.href = url;
        a.download = 'cardstock-portfolio.csv';
        a.click();
        URL.revokeObjectURL(url);
    };

    const sortTh = (label: string, k: string, opts: { ascFirst?: boolean; mid?: boolean } = {}) => (
        <SortTh label={label} k={k} ascFirst={opts.ascFirst}
            className={(opts.mid ?? true) ? "screener__mid" : undefined}
            orderBy={params.orderBy ?? ''} onSort={v => dispatch(setOrderBy(v))} />
    );

    return (
        <>
            {/* ----- Header: total value + change pills + value chart ----- */}
            {!empty && (
            <div className="pf-hero">
                <div className="pf-hero__value" aria-label="Portfolio value">
                    {summary ? currencyFormat(summary.totalValue) : 'n/a'}
                </div>
                <div className="pf-hero__pills">
                    {summary?.monthChangeUsd != null && (
                        <ChangePill value={summary.monthChangeUsd} unit="usd" suffix={
                            (summary.monthChangePct != null
                                ? `(${summary.monthChangePct >= 0 ? '+' : '−'}${Math.abs(summary.monthChangePct).toFixed(1)}%) `
                                : '') + 'this month'
                        } />
                    )}
                    {summary?.allTime && (
                        <ChangePill value={summary.allTime.plPct} suffix="on what you paid" />
                    )}
                    {summary?.realized && (
                        <ChangePill value={summary.realized.plUsd} unit="usd" suffix="from sales"
                            title={`Profit or loss across ${summary.realized.count} sale${summary.realized.count === 1 ? '' : 's'}`} />
                    )}
                </div>
                {summary && <ValueChart summary={summary}
                    includePacks={includePacks} onIncludePacks={setIncludePacks} />}
            </div>
            )}

            {/* ----- Right rail: allocation + best/worst ----- */}
            {!empty && summary && (
            <div className="pf-side">
                <AllocationDonut title="Allocation · games" slices={summary.allocation ?? []} />
                <AllocationDonut title="Allocation · grades" slices={summary.gradeAllocation ?? []} />
                <BestWorst summary={summary} />
            </div>
            )}

            {/* ----- Positions table ----- */}
            <div className={`pf-positions full-span${empty && soldCount === 0 ? ' pf-positions--empty' : ''}`}>
                <div className="table-head">
                    <div className="pf-tabs" role="tablist" aria-label="Portfolio tracks">
                        <button type="button" role="tab" aria-selected={view === 'positions'}
                            className="pf-tab" onClick={() => showView('positions')}>
                            Positions{summary ? ` · ${summary.copies}` : ''}
                        </button>
                        {(listedCount > 0 || view === 'listed') && (
                            <button type="button" role="tab" aria-selected={view === 'listed'}
                                className="pf-tab" onClick={() => showView('listed')}>
                                Listed{listedCount ? ` · ${listedCount}` : ''}
                            </button>
                        )}
                        <button type="button" role="tab" aria-selected={view === 'sold'}
                            className="pf-tab" onClick={() => showView('sold')}>
                            Sold{soldCount ? ` · ${soldCount}` : ''}
                        </button>
                    </div>
                    {/* Actions on their own row beneath the tabs (user 2026-10-25). */}
                    <div className="pf-actions">
                        {!empty && view === 'positions' && (
                            <button className="btn btn--outline" onClick={exportCsv}>
                                Export
                            </button>
                        )}
                        <Link className="btn btn--outline" to="/scan" title="Add cards with your camera">
                            Scan
                        </Link>
                        <button className="btn btn--outline" onClick={() => setShowImport(true)}>
                            Import
                        </button>
                        <ToolsMenu items={[
                            { label: 'Collection analysis', hint: 'Infographics about everything you hold',
                              onSelect: () => setShowAnalysis(true) },
                            { label: 'Value outlook', hint: 'What your cards could be worth in 1 month, 6 months and 1 year',
                              onSelect: () => setShowOutlook(true) },
                            { label: 'Recommend grading', hint: 'Near Mint cards that gain most from PSA grading',
                              onSelect: () => setShowGrading(true) },
                        ]} />
                        <button className="btn btn--outline btn--circle" title="How is Paid set?"
                            onClick={() => setShowPaidHelp(true)}>?</button>
                    </div>
                </div>
                {showImport && <ImportModal onClose={() => setShowImport(false)} />}
                {showGrading && <GradingModal onClose={() => setShowGrading(false)} />}
                {showAnalysis && <CollectionAnalysisModal onClose={() => setShowAnalysis(false)} />}
                {showOutlook && <OutlookModal includePacks={includePacks} onClose={() => setShowOutlook(false)} />}
                {showPaidHelp && <PaidHelpModal onClose={() => setShowPaidHelp(false)} />}

                {view === 'sold' ? (
                    <SoldTable realized={summary?.realized} />
                ) : (
                <>
                {/* No "Price shown" tier picker here: positions already price at
                    each copy's own condition. Hidden entirely while the
                    portfolio has nothing to filter. */}
                {!empty && (
                    <TrackedFilters params={params} actions={ownedParamsSlice.actions}
                        sortGroups={trackedSortGroups} showGrade={false} />
                )}

                {isLoading ? (
                    <CardLoader />
                ) : isError && !currentData ? (
                    // The page asked for failed (data would still hold the
                    // previous page, which reads as a pager that does nothing).
                    <div className="est-note load-error">
                        <span>We couldn't load this page of your portfolio.</span>
                        <button className="btn btn--outline" onClick={() => refetch()}>Try again</button>
                    </div>
                ) : data && data.items.length > 0 ? (
                    <>
                        <div className="screener-wrap">
                            <table className="screener screener--positions">
                                <thead>
                                    <tr>
                                        <th aria-label="Card image" />
                                        {sortTh("Card", "name", { ascFirst: true, mid: false })}
                                        {sortTh("Condition", "condition")}
                                        {sortTh("Qty", "qty")}
                                        {sortTh("Paid", "paid")}
                                        {sortTh("Value", "value")}
                                        {sortTh("Gain", "pl")}
                                        {sortTh(`${hasYear ? '1Y' : '6M'} forecast`, hasYear ? 'chgPct12' : 'chgPct6')}
                                        {sortTh("Trend", `histPct${params.trend ?? '1m'}`)}
                                        <th aria-label="Actions" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map(card => (
                                        <PositionRow card={card} hasYear={hasYear} key={
                                            // a stack is identified by its oldest copy (stable while it exists)
                                            `${card.id}:${card.ownedGrade ?? ''}:${card.ownedCopies?.[0]?.id ?? 'x'}`
                                        } />
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <AppPagination
                            metadata={data.pagination}
                            onPageChange={(page: number) => dispatch(setPageNumber(page))}
                        />
                    </>
                ) : view === 'listed' ? (
                    <p className="est-note">
                        Nothing here is marked as listed for sale. Tap the tag on any
                        position to mark it.
                    </p>
                ) : (
                    <p className="est-note">
                        No cards in your portfolio yet. Browse the <Link to="/catalog">catalog</Link> and
                        tap "＋ Add" on any card.
                    </p>
                )}
                </>
                )}
            </div>
        </>
    );
}
