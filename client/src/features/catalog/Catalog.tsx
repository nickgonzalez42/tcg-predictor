import { useFetchFiltersQuery, useFetchCardsQuery } from "./catalogApi"
import CardList from "./CardList"
import Filters from "./Filters";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import AppPagination from "../../app/shared/components/AppPagination";
import { setPageNumber } from "./catalogSlice";

export default function Catalog() {
  const cardParams = useAppSelector(state => state.catalog);
  const { data: filtersData, isLoading: filtersLoading } = useFetchFiltersQuery(cardParams.game);
  const { data, isLoading } = useFetchCardsQuery(cardParams);
  const dispatch = useAppDispatch();

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
