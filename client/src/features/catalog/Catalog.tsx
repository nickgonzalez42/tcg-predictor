import { useFetchFiltersQuery, useFetchCardsQuery } from "./catalogApi"
import CardList from "./CardList"
import CardTable from "./CardTable"
import Filters from "./Filters";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import AppPagination from "../../app/shared/components/AppPagination";
import AdSlot from "../../app/shared/components/AdSlot";
import CardLoader from "../../app/shared/components/CardLoader";
import UpdatingList from "../../app/shared/components/UpdatingList";
import { DEFAULT_ORDER, DEFAULT_PAGE_SIZE, DEFAULT_TREND, applyDefaultSets, fitWindows, initDefaultGame, resetParams, resetToDefaults, setPageNumber, setParams, setSearchTerm, setTrend, setView, setQuickAdd } from "./catalogSlice";
import { WINDOW_LABEL, windowsFor } from "./windows";
import type { CardParams } from "../../app/models/cardParams";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useUserInfoQuery } from "../account/accountApi";
import { useFetchWatchlistQuery } from "../watchlist/watchlistApi";
import { useMediaQuery } from "../../lib/useMediaQuery";
import { usePageMeta } from "../../lib/usePageMeta";
import { GAME_LABEL } from "../../lib/games";

export default function Catalog() {
    usePageMeta("Card Prices and Predictions",
        "Browse and screen Magic, Pokémon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, and Gundam cards by price prediction, trend, and graded tier.");
  const cardParams = useAppSelector(state => state.catalog);
  const marketMode = cardParams.market ?? '';
  const pre = marketMode === 'without';
  // Nothing is fetched for the placeholder game: both queries wait for the
  // session's game decision (URL, a click, or the portfolio majority below),
  // so no facets or cards for the wrong game are ever requested or shown.
  const { data: filtersData, currentData: filtersNow, isLoading: filtersLoading } = useFetchFiltersQuery(
    { game: cardParams.game, market: marketMode || undefined }, { skip: !cardParams.gameInitialized });
  // The list waits until the set filter is settled (the newest set by
  // default, see below), so a visitor never sees every set flash past first.
  // New filters keep the old list on screen until the new one lands
  // (data = the last answer, currentData = this filter's, once it's in);
  // `updating` dims it meanwhile.
  const { data, currentData, isLoading, isFetching } = useFetchCardsQuery(cardParams,
    { skip: !cardParams.gameInitialized || !cardParams.setsDecided });
  const updating = isFetching && !currentData;
  const dispatch = useAppDispatch();
  // Windows the game can't serve (no 6m/12m rows yet, or under a year of
  // history) lose their chip and their sorts; the active sort and trend step
  // down to a window it has and come back on their own once THIS game's
  // facets (currentData, never a stale game's) say the rows exist. Pre-release
  // mode sorts on the estimate alone and is left alone.
  const windows = windowsFor(filtersNow);
  useEffect(() => {
    if (pre || !filtersNow) return;
    dispatch(fitWindows(windowsFor(filtersNow)));
  }, [pre, filtersNow, cardParams.orderBy, cardParams.trend, dispatch]);
  const [searchParams, setSearchParams] = useSearchParams();

  // On mount, decide from the URL:
  //  - has params (back button, breadcrumb game crumb, shared/bookmarked
  //    link) -> the URL is the source of truth: start from defaults and apply
  //    exactly what it carries, so a partial URL can't inherit stale filters.
  //  - bare /catalog (a nav-link click) -> keep whatever this session already
  //    set; filters survive leaving and returning. A first visit has pristine
  //    defaults and the portfolio-majority effect below picks the game.
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const p: Partial<CardParams> = {};
    const get = (k: string) => searchParams.get(k) || undefined;
    if (get('game')) { p.game = get('game')!; p.gameInitialized = true; }
    if (get('orderBy')) p.orderBy = get('orderBy')!;
    if (get('searchTerm')) p.searchTerm = get('searchTerm')!;
    // sets=all is the "every set" mark: with the newest set as the default, a
    // cleared filter needs a sign of its own to survive a reload.
    if (get('sets')) { p.sets = get('sets') === 'all' ? [] : get('sets')!.split(','); p.setsDecided = 'user'; }
    if (get('rarities')) p.rarities = get('rarities')!.split(',');
    // 'any' was the filter-cleared sentinel while High was the default; it now
    // equals the default but old shared links still carry it.
    if (get('confidence')) p.confidence = get('confidence') === 'any' ? [] : get('confidence')!.split(',');
    if (get('printings')) p.printings = get('printings')!.split(',');
    if (get('grade')) p.grade = get('grade')!;
    // minPrice=0 was the floor-cleared sentinel while a default floor existed
    // ($10, then $1 until 2026-11-02); there is no floor now, but old shared
    // links still carry it.
    if (get('minPrice')) p.minPrice = get('minPrice') === '0' ? '' : get('minPrice')!;
    if (get('maxPrice')) p.maxPrice = get('maxPrice')!;
    if (get('pageNumber')) p.pageNumber = +get('pageNumber')!;
    if (get('pageSize')) p.pageSize = +get('pageSize')!;
    if (get('trend')) p.trend = get('trend')!;
    if (get('market')) p.market = ['without', 'all'].includes(get('market')!) ? get('market')! : '';
    // Legacy pre-release links (2026-10-10's boolean) map onto the new mode.
    if (get('prerelease') === '1') p.market = 'without';
    if (get('view')) p.view = get('view') === 'rows' ? 'rows' : 'cards';
    if (Object.keys(p).length) {
      dispatch(resetToDefaults());
      dispatch(setParams(p));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // First visit with no ?game= : default to the game the user owns the most
  // cards in; Pokémon when signed out or the portfolio is empty. Decided once
  // per session — an explicit choice or URL param always wins.
  const { data: user, isLoading: userLoading } = useUserInfoQuery();
  const { data: watchlist, isLoading: watchlistLoading } = useFetchWatchlistQuery(undefined, { skip: !user });
  const siteGame = useAppSelector(state => state.site.game);
  useEffect(() => {
    // A URL ?game= marks gameInitialized during hydration, and the reducer
    // itself is a no-op once any decision exists — so this can't override one.
    if (cardParams.gameInitialized) return;
    // The game picked in the nav (or set by a first-visit article) needs no
    // account facts: it applies at once.
    if (siteGame) { dispatch(initDefaultGame(siteGame)); return; }
    if (userLoading || (user && watchlistLoading)) return;   // decide once the facts are in
    const owned: Record<string, number> = {};
    for (const w of watchlist ?? [])
      if (w.kind === 'owned') owned[w.game] = (owned[w.game] ?? 0) + 1;
    const best = Object.entries(owned).sort((a, b) => b[1] - a[1])[0];
    dispatch(initDefaultGame(best ? best[0] : 'pokemon'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, userLoading, watchlist, watchlistLoading, cardParams.gameInitialized, siteGame]);

  // Opening set (2026-11-02): once THIS game's facets are in (currentData, so
  // a stale game's answer can't apply), the newest set becomes the filter
  // unless the visitor already settled it. Pre-release mode has no "newest"
  // that means anything, so it stays open.
  useEffect(() => {
    if (cardParams.setsDecided || !filtersNow) return;
    dispatch(applyDefaultSets({ game: cardParams.game, set: pre ? null : (filtersNow.latestSet ?? null) }));
  }, [cardParams.setsDecided, cardParams.game, filtersNow, pre, dispatch]);

  // Reflect catalog state in the URL whenever it changes.
  useEffect(() => {
    const sp: Record<string, string> = { game: cardParams.game };
    // The sort and window the visitor ASKED for go in the URL, not the ones
    // the catalog stepped down to: a link shared from a game with 1M rows only
    // opens on 6M by itself once the game has them.
    const orderOut = cardParams.orderWish ?? cardParams.orderBy;
    if (orderOut && orderOut !== DEFAULT_ORDER) sp.orderBy = orderOut;
    if (cardParams.searchTerm) sp.searchTerm = cardParams.searchTerm;
    // Sets: only the visitor's own choice goes in the URL ('all' when they
    // cleared the default), so a shared bare link opens on whatever set is
    // newest at the time, like the catalog itself.
    if (cardParams.setsDecided === 'user') sp.sets = cardParams.sets.length ? cardParams.sets.join(',') : 'all';
    if (cardParams.rarities.length) sp.rarities = cardParams.rarities.join(',');
    if (cardParams.confidence.length) sp.confidence = cardParams.confidence.join(',');
    if (cardParams.printings.length) sp.printings = cardParams.printings.join(',');
    if (cardParams.grade) sp.grade = cardParams.grade;
    if (cardParams.minPrice) sp.minPrice = cardParams.minPrice;
    if (cardParams.maxPrice) sp.maxPrice = cardParams.maxPrice;
    if (cardParams.pageNumber > 1) sp.pageNumber = String(cardParams.pageNumber);
    if (cardParams.pageSize !== DEFAULT_PAGE_SIZE) sp.pageSize = String(cardParams.pageSize);
    const trendOut = cardParams.trendWish ?? cardParams.trend;
    if (trendOut && trendOut !== DEFAULT_TREND) sp.trend = trendOut;
    if (cardParams.market) sp.market = cardParams.market;
    if (cardParams.view === 'rows') sp.view = 'rows';
    setSearchParams(sp, { replace: true });
  }, [cardParams, setSearchParams]);

  // Mobile: the 6-column screener can't work at phone width, so row view is
  // disabled and cards are forced. The stored preference isn't touched — a
  // rows user gets rows back the moment the viewport grows past 767px.
  const isMobile = useMediaQuery('(max-width: 767px)');
  // Scan link only where a camera is at hand: touchscreen phones/tablets
  // (coarse pointer, no hover) below the desktop breakpoint (user 2026-10-25).
  const touchDevice = useMediaQuery('(hover: none) and (pointer: coarse) and (max-width: 1023px)');

  if (isLoading || !data || filtersLoading || !filtersData) return <CardLoader game={cardParams.game} />

  const view = isMobile ? 'cards' : (cardParams.view ?? 'cards');
  const quick = !!user && !!cardParams.quickAdd;
  const totalCount = data.pagination?.totalCount;
  // Empty results: say what was searched for, in which game (search covers
  // one game at a time), and offer the way out. "Narrowed" = anything beyond
  // the search that could be hiding the card; the opening set doesn't count,
  // a search already set it aside.
  const term = cardParams.searchTerm?.trim();
  const gameLabel = GAME_LABEL[cardParams.game] ?? '';
  const narrowed = (cardParams.setsDecided === 'user' && cardParams.sets.length > 0)
    || cardParams.rarities.length > 0 || cardParams.printings.length > 0
    || cardParams.confidence.length > 0 || !!cardParams.minPrice || !!cardParams.maxPrice;
  // Clears every filter but keeps the search: that card is still the goal.
  const clearFiltersKeepSearch = () => {
    dispatch(resetParams());
    if (cardParams.searchTerm) dispatch(setSearchTerm(cardParams.searchTerm));
  };

  return (
    <div className={`catalog subgrid full-span${view === 'rows' ? ' catalog--rows' : ''}`}>
      <Filters filtersData={filtersData} />
      <div className="catalog-items subgrid">
        <div className="results-head full-span">
          <span className="mono">
            {totalCount != null ? `${totalCount.toLocaleString('en-US')} CARDS` : ' '}
          </span>
          {/* Pre-release mode has no history to window, so the trend chips are
              hidden. Nothing stands in their place: every tile already carries
              the PREDICTED tag and the filter rail explains the mode. */}
          {!pre && (
            <div className="range-tabs" role="group" aria-label="Trend period"
              title="Window for the forecast shown, the trend line and the price movement; a forecast or past-change sort follows it">
              {(['1m', '6m', '1y'] as const).map(t => (
                <button key={t}
                  className={`btn btn--outline range-tab${(cardParams.trend ?? '1m') === t ? ' btn--active' : ''}`}
                  onClick={() => dispatch(setTrend(t))}
                  aria-pressed={(cardParams.trend ?? '1m') === t}
                  disabled={!windows[t]}
                  title={!windows[t]
                    ? (t === '1y' && filtersNow?.hasYear === false
                        ? 'This game has under a year of price data'
                        : `No ${WINDOW_LABEL[t]} calls for this game yet`)
                    : undefined}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
          )}
          {/* Camera scanner (2026-10-25): sits with the collection controls;
              signed-out visitors land on the login page via RequireAuth. */}
          {touchDevice && (
            <Link className="btn btn--outline results-head__scan" to="/scan"
              title="Add cards to your collection with your camera">
              Scan
            </Link>
          )}
          {user && (
            <label className="switch-field"
              title={quick
                ? 'Quick add is on: one click adds a copy (pack pull) at the selected condition'
                : 'Quick add: show an Add button on every card, no reveal'}>
              <span className="switch-field__label">Quick add</span>
              <button type="button" role="switch" aria-checked={quick}
                className={`switch${quick ? ' switch--on' : ''}`}
                onClick={() => dispatch(setQuickAdd(!quick))}>
                <span className="switch__knob" />
              </button>
            </label>
          )}
          {!isMobile && (
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
          )}
        </div>
        <UpdatingList updating={updating} className="subgrid full-span">
          {data.items && data.items.length > 0 ? (
            <>
              {view === 'rows' ? (
                <CardTable cards={data.items} ownGrade={cardParams.grade ?? ''} trend={cardParams.trend} quick={quick} />
              ) : (
                <CardList cards={data.items} ownGrade={cardParams.grade ?? ''} quick={quick} />
              )}
              <AppPagination
                metadata={data.pagination}
                onPageChange={(page: number) => dispatch(setPageNumber(page))}
              />
            </>
          ) : term ? (
            <div className="catalog-empty full-span">
              <h3 className="catalog-empty__title">
                No {pre ? 'pre-release ' : ''}{gameLabel} cards match “{term}”
              </h3>
              <p className="catalog-empty__text">
                {narrowed
                  ? `Your filters might be hiding it. Clear them and we'll look through every ${gameLabel} card for it.`
                  : `Check the spelling, or try part of the name. Search covers one game at a time, so if it's not a ${gameLabel} card, switch games in Filters.`}
              </p>
              <div className="catalog-empty__actions">
                <button className="btn btn--outline" onClick={() => dispatch(setSearchTerm(''))}>
                  Clear search
                </button>
                {narrowed && (
                  <button className="btn btn--outline" onClick={clearFiltersKeepSearch}>
                    Clear filters
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="catalog-empty full-span">
              <h3 className="catalog-empty__title">
                {pre ? 'No pre-release cards for this game right now' : 'No cards match these filters'}
              </h3>
              {narrowed && (
                <>
                  <p className="catalog-empty__text">Try a wider price range, or clear a filter or two.</p>
                  <div className="catalog-empty__actions">
                    <button className="btn btn--outline" onClick={() => dispatch(resetParams())}>
                      Clear filters
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </UpdatingList>
        {data.items && data.items.length > 0 && <AdSlot slot="" />}
      </div>
    </div>
  )
}
