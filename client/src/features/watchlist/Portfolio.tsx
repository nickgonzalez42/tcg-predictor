import { useState } from "react";
import { Link } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { useFetchTrackedCardsQuery, useFetchPortfolioSummaryQuery } from "./watchlistApi";
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
import ClearPortfolioModal from "./ClearPortfolioModal";
import PaidHelpModal from "./PaidHelpModal";

export default function Portfolio() {
    usePageMeta("Portfolio");
    const [showPaidHelp, setShowPaidHelp] = useState(false);
    const [showImport, setShowImport] = useState(false);
    const [showClear, setShowClear] = useState(false);
    const { setPageNumber, setOrderBy } = ownedParamsSlice.actions;
    const params = useAppSelector(state => state.ownedParams);
    const dispatch = useAppDispatch();

    const { data, isLoading } = useFetchTrackedCardsQuery({ kind: 'owned', ...params });
    const { data: summary } = useFetchPortfolioSummaryQuery();
    // Young games (digimon/gundam) have no 12m horizon yet — the forecast
    // column falls back to their 6m numbers and relabels itself.
    const { data: filtersData } = useFetchFiltersQuery(params.game);
    const hasYear = filtersData?.hasYear ?? true;

    // A truly empty portfolio (no copies at all, filters aside) skips the page
    // furniture — chart hero, allocation rail, filters — and shows just the
    // empty state with the Import onramp.
    const empty = summary != null && summary.copies === 0;

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
                <span className="mono">Portfolio value</span>
                <div className="pf-hero__value">
                    {summary ? currencyFormat(summary.totalValue) : '—'}
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
                        <ChangePill value={summary.allTime.plPct} suffix="vs cost" />
                    )}
                </div>
                {summary && <ValueChart summary={summary} />}
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
            <div className="pf-positions full-span">
                <div className="table-head">
                    <h2 className="table-head__title">Positions</h2>
                    {!empty && (
                        <button className="btn btn--outline" onClick={exportCsv}>
                            Export
                        </button>
                    )}
                    <button className="btn btn--outline" onClick={() => setShowImport(true)}>
                        Import
                    </button>
                    <button className="btn btn--outline btn--circle" title="How is Paid set?"
                        onClick={() => setShowPaidHelp(true)}>?</button>
                    {!empty && (
                        <button className="btn btn--outline btn--danger" title="Delete every position"
                            onClick={() => setShowClear(true)}>
                            Clear
                        </button>
                    )}
                </div>
                {showImport && <ImportModal onClose={() => setShowImport(false)} />}
                {showClear && (
                    <ClearPortfolioModal copies={summary?.copies ?? 0}
                        onClose={() => setShowClear(false)} />
                )}
                {showPaidHelp && <PaidHelpModal onClose={() => setShowPaidHelp(false)} />}

                {/* No "Price shown" tier picker here: positions already price at
                    each copy's own condition. Hidden entirely while the
                    portfolio has nothing to filter. */}
                {!empty && (
                    <TrackedFilters params={params} actions={ownedParamsSlice.actions}
                        sortGroups={trackedSortGroups} showGrade={false} />
                )}

                {isLoading ? (
                    <CardLoader />
                ) : data && data.items.length > 0 ? (
                    <>
                        <div className="screener-wrap">
                            <table className="screener">
                                <thead>
                                    <tr>
                                        <th aria-label="Card image" />
                                        {sortTh("Card", "name", { ascFirst: true, mid: false })}
                                        {sortTh("Condition", "condition")}
                                        {sortTh("Qty", "qty")}
                                        {sortTh("Paid", "paid")}
                                        {sortTh("Mkt value", "value")}
                                        {sortTh("P/L", "pl")}
                                        {sortTh(`${hasYear ? '1Y' : '6M'} fcst`, hasYear ? 'chgPct12' : 'chgPct6')}
                                        {sortTh("Trend", `histPct${params.trend ?? '1m'}`)}
                                        <th aria-label="Actions" />
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map(card => (
                                        <PositionRow card={card} hasYear={hasYear} key={
                                            `${card.id}:${card.ownedGrade ?? ''}:` +
                                            (card.ownedCopies?.length === 1 ? card.ownedCopies[0].id : 'stack')
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
                ) : (
                    <p className="est-note">
                        No cards in your portfolio yet. Browse the <Link to="/catalog">catalog</Link> and
                        tap "＋ Add" on any card.
                    </p>
                )}
            </div>
        </>
    );
}
