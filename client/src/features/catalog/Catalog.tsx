import { useFetchFiltersQuery, useFetchCardsQuery } from "./catalogApi"
import CardList from "./CardList"
import CardTable from "./CardTable"
import Filters from "./Filters";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import AppPagination from "../../app/shared/components/AppPagination";
import CardLoader from "../../app/shared/components/CardLoader";
import { DEFAULT_ORDER, DEFAULT_PAGE_SIZE, initDefaultGame, setPageNumber, setParams, setTrend, setView } from "./catalogSlice";
import type { CardParams } from "../../app/models/cardParams";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useUserInfoQuery } from "../account/accountApi";
import { useFetchWatchlistQuery } from "../watchlist/watchlistApi";

export default function Catalog() {
  const cardParams = useAppSelector(state => state.catalog);
  const { data: filtersData, isLoading: filtersLoading } = useFetchFiltersQuery(cardParams.game);
  const { data, isLoading } = useFetchCardsQuery(cardParams);
  const dispatch = useAppDispatch();
  const [searchParams, setSearchParams] = useSearchParams();

  // Hydrate catalog state from the URL once on mount (shareable/bookmarkable links).
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const p: Partial<CardParams> = {};
    const get = (k: string) => searchParams.get(k) || undefined;
    if (get('game')) { p.game = get('game')!; p.gameInitialized = true; }
    if (get('orderBy')) p.orderBy = get('orderBy')!;
    if (get('searchTerm')) p.searchTerm = get('searchTerm')!;
    if (get('sets')) p.sets = get('sets')!.split(',');
    if (get('rarities')) p.rarities = get('rarities')!.split(',');
    if (get('grade')) p.grade = get('grade')!;
    if (get('pageNumber')) p.pageNumber = +get('pageNumber')!;
    if (get('pageSize')) p.pageSize = +get('pageSize')!;
    if (get('trend')) p.trend = get('trend')!;
    if (get('view')) p.view = get('view') === 'rows' ? 'rows' : 'cards';
    if (Object.keys(p).length) dispatch(setParams(p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // First visit with no ?game= : default to the game the user owns the most
  // cards in; Pokémon when signed out or the portfolio is empty. Decided once
  // per session — an explicit choice or URL param always wins.
  const { data: user, isLoading: userLoading } = useUserInfoQuery();
  const { data: watchlist, isLoading: watchlistLoading } = useFetchWatchlistQuery(undefined, { skip: !user });
  useEffect(() => {
    // A URL ?game= marks gameInitialized during hydration, and the reducer
    // itself is a no-op once any decision exists — so this can't override one.
    if (cardParams.gameInitialized) return;
    if (userLoading || (user && watchlistLoading)) return;   // decide once the facts are in
    const owned: Record<string, number> = {};
    for (const w of watchlist ?? [])
      if (w.kind === 'owned') owned[w.game] = (owned[w.game] ?? 0) + 1;
    const best = Object.entries(owned).sort((a, b) => b[1] - a[1])[0];
    dispatch(initDefaultGame(best ? best[0] : 'pokemon'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, userLoading, watchlist, watchlistLoading, cardParams.gameInitialized]);

  // Reflect catalog state in the URL whenever it changes.
  useEffect(() => {
    const sp: Record<string, string> = { game: cardParams.game };
    if (cardParams.orderBy && cardParams.orderBy !== DEFAULT_ORDER) sp.orderBy = cardParams.orderBy;
    if (cardParams.searchTerm) sp.searchTerm = cardParams.searchTerm;
    if (cardParams.sets.length) sp.sets = cardParams.sets.join(',');
    if (cardParams.rarities.length) sp.rarities = cardParams.rarities.join(',');
    if (cardParams.grade) sp.grade = cardParams.grade;
    if (cardParams.pageNumber > 1) sp.pageNumber = String(cardParams.pageNumber);
    if (cardParams.pageSize !== DEFAULT_PAGE_SIZE) sp.pageSize = String(cardParams.pageSize);
    if (cardParams.trend && cardParams.trend !== '1y') sp.trend = cardParams.trend;
    if (cardParams.view === 'rows') sp.view = 'rows';
    setSearchParams(sp, { replace: true });
  }, [cardParams, setSearchParams]);

  if (isLoading || !data || filtersLoading || !filtersData) return <CardLoader game={cardParams.game} />

  const view = cardParams.view ?? 'cards';
  const totalCount = data.pagination?.totalCount;

  return (
    <div className={`catalog subgrid full-span${view === 'rows' ? ' catalog--rows' : ''}`}>
      <Filters filtersData={filtersData} />
      <div className="catalog-items subgrid">
        <div className="results-head full-span">
          <span className="mono">
            {totalCount != null ? `${totalCount.toLocaleString('en-US')} CARDS` : ' '}
          </span>
          <div className="range-tabs" role="group" aria-label="Trend period"
            title="Window for the trend line and price movement (price data updates monthly)">
            {(['1w', '1m', '6m', '1y'] as const).map(t => (
              <button key={t}
                className={`btn btn--outline range-tab${(cardParams.trend ?? '1m') === t ? ' btn--active' : ''}`}
                onClick={() => dispatch(setTrend(t))}
                aria-pressed={(cardParams.trend ?? '1m') === t}
              >
                {t.toUpperCase()}
              </button>
            ))}
          </div>
          <div className="view-toggle" role="group" aria-label="Results view">
            <button
              className={`btn btn--outline view-toggle__btn${view === 'cards' ? ' btn--active' : ''}`}
              onClick={() => dispatch(setView('cards'))}
              aria-pressed={view === 'cards'}
            >
              ▦ Cards
            </button>
            <button
              className={`btn btn--outline view-toggle__btn${view === 'rows' ? ' btn--active' : ''}`}
              onClick={() => dispatch(setView('rows'))}
              aria-pressed={view === 'rows'}
            >
              ☰ Rows
            </button>
          </div>
        </div>
        {data.items && data.items.length > 0 ? (
          <>
            {view === 'rows' ? (
              <CardTable cards={data.items} ownGrade={cardParams.grade ?? ''} trend={cardParams.trend} />
            ) : (
              <CardList cards={data.items} ownGrade={cardParams.grade ?? ''} />
            )}
            <AppPagination
              metadata={data.pagination}
              onPageChange={(page: number) => dispatch(setPageNumber(page))}
            />
          </>
        ) : (
          <h3>There are no results for this filter</h3>
        )}
      </div>
    </div>
  )
}
