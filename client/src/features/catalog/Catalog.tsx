import { useFetchFiltersQuery, useFetchCardsQuery } from "./catalogApi"
import CardList from "./CardList"
import Filters from "./Filters";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import AppPagination from "../../app/shared/components/AppPagination";
import { setPageNumber, setParams } from "./catalogSlice";
import type { CardParams } from "../../app/models/cardParams";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

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
    if (get('game')) p.game = get('game')!;
    if (get('orderBy')) p.orderBy = get('orderBy')!;
    if (get('searchTerm')) p.searchTerm = get('searchTerm')!;
    if (get('sets')) p.sets = get('sets')!.split(',');
    if (get('rarities')) p.rarities = get('rarities')!.split(',');
    if (get('pageNumber')) p.pageNumber = +get('pageNumber')!;
    if (get('pageSize')) p.pageSize = +get('pageSize')!;
    if (Object.keys(p).length) dispatch(setParams(p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reflect catalog state in the URL whenever it changes.
  useEffect(() => {
    const sp: Record<string, string> = { game: cardParams.game };
    if (cardParams.orderBy && cardParams.orderBy !== 'name') sp.orderBy = cardParams.orderBy;
    if (cardParams.searchTerm) sp.searchTerm = cardParams.searchTerm;
    if (cardParams.sets.length) sp.sets = cardParams.sets.join(',');
    if (cardParams.rarities.length) sp.rarities = cardParams.rarities.join(',');
    if (cardParams.pageNumber > 1) sp.pageNumber = String(cardParams.pageNumber);
    if (cardParams.pageSize !== 50) sp.pageSize = String(cardParams.pageSize);
    setSearchParams(sp, { replace: true });
  }, [cardParams, setSearchParams]);

  if (isLoading || !data || filtersLoading || !filtersData) return <div>Is loading...</div>

  return (
    <div className="catalog">
      <Filters filtersData={filtersData} />
      <div>
        {data.items && data.items.length > 0 ? (
          <>
            <CardList cards={data.items} />
            <AppPagination
              metadata={data.pagination}
              onPageChange={(page: number) => {
                dispatch(setPageNumber(page));
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
            />
          </>
        ) : (
          <h3>There are no results for this filter</h3>
        )}
      </div>
    </div>
  )
}
