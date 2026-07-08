import { useEffect, useRef, useState } from "react";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { useFetchFiltersQuery } from "../catalog/catalogApi";
import { useFetchTrackedCardsQuery, type TrackKind } from "./watchlistApi";
import { ownedParamsSlice, wishlistParamsSlice } from "./trackedParamsSlice";
import CardList from "../catalog/CardList";
import OwnedConditionItem from "./OwnedConditionItem";
import { catalogGradeToCondition } from "./grades";
import { forecastSortOptions } from "../catalog/sortOptions";
import AppPagination from "../../app/shared/components/AppPagination";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import MultiSelectDropdown from "../../app/shared/components/MultiSelectDropdown";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";

const sortOptions = [
    { value: '', label: 'Recently added' },
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: high to low' },
    { value: 'price', label: 'Price: low to high' },
    ...forecastSortOptions,
];
const gameOptions = [
    { value: 'onepiece', label: 'One Piece' },
    { value: 'pokemon', label: 'Pokémon' },
];
const gradeOptions = [
    { value: '', label: 'Near Mint' },
    { value: 'lp', label: 'Lightly Played' },
    { value: 'mp', label: 'Moderately Played' },
    { value: 'grade7', label: 'Grade 7' },
    { value: 'grade8', label: 'Grade 8' },
    { value: 'grade9', label: 'Grade 9' },
    { value: 'grade95', label: 'Grade 9.5' },
    { value: 'psa10', label: 'PSA 10' },
];

function DebouncedSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
    const [term, setTerm] = useState(value);
    const timeout = useRef<ReturnType<typeof setTimeout>>(undefined);

    useEffect(() => { setTerm(value); }, [value]);

    const handle = (v: string) => {
        setTerm(v);
        if (timeout.current) clearTimeout(timeout.current);
        timeout.current = setTimeout(() => onChange(v), 500);
    };

    return (
        <div className="field" style={{ margin: 0 }}>
            <label htmlFor="tracked-search">Search cards</label>
            <input id="tracked-search" className="input" type="search"
                value={term} onChange={e => handle(e.target.value)} />
        </div>
    );
}

type Props = { kind: TrackKind; title: string };

export default function TrackedList({ kind, title }: Props) {
    const slice = kind === 'owned' ? ownedParamsSlice : wishlistParamsSlice;
    const { setGame, setOrderBy, setSets, setRarities, setSearchTerm, setGrade, setPageNumber, resetParams }
        = slice.actions;

    const params = useAppSelector(state => kind === 'owned' ? state.ownedParams : state.wishlistParams);
    const dispatch = useAppDispatch();

    const { data: filtersData } = useFetchFiltersQuery(params.game);
    const { data, isLoading } = useFetchTrackedCardsQuery({ kind, ...params });

    const emptyHint = kind === 'owned'
        ? "No cards in your portfolio yet — browse the catalog and tap “＋ Add” on any card."
        : "No cards on your wishlist yet — browse the catalog and tap “☆ Wishlist” on any card.";

    return (
        <div className="catalog subgrid full-span">
            <div className="filters">
                <h2 style={{ margin: 0 }}>{title}</h2>
                <div className="panel">
                    <DebouncedSearch value={params.searchTerm ?? ''} onChange={v => dispatch(setSearchTerm(v))} />
                </div>
                {/* Owned tiles are already per-condition, so the price-tier picker only applies to Wishlist. */}
                {kind !== 'owned' && (
                    <div className="panel">
                        <label htmlFor="tracked-grade" className="field-label">Price shown</label>
                        <select id="tracked-grade" className="input" value={params.grade ?? ''}
                            onChange={e => dispatch(setGrade(e.target.value))}>
                            {gradeOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                    </div>
                )}
                <div className="panel">
                    <RadioButtonGroup selectedValue={params.game} options={gameOptions}
                        onChange={e => dispatch(setGame(e.target.value))} />
                </div>
                <div className="panel">
                    <label htmlFor="tracked-sort" className="field-label">Sort by</label>
                    <select id="tracked-sort" className="input" value={params.orderBy}
                        onChange={e => dispatch(setOrderBy(e.target.value))}>
                        {sortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </div>
                {filtersData && (
                    <>
                        <div className="panel">
                            <CheckBoxButtons items={filtersData.rarities} checked={params.rarities}
                                onChange={(items: string[]) => dispatch(setRarities(items))} />
                        </div>
                        <div className="panel">
                            <MultiSelectDropdown label="Sets" items={filtersData.sets} checked={params.sets}
                                onChange={(items: string[]) => dispatch(setSets(items))} />
                        </div>
                    </>
                )}
                <button className="btn btn--outline" onClick={() => dispatch(resetParams())}>Reset filters</button>
            </div>

            <div className="catalog-items subgrid">
                {isLoading ? (
                    <div>Loading...</div>
                ) : data && data.items.length > 0 ? (
                    <>
                        {kind === 'owned' ? (
                            <div className="product-grid subgrid full-span">
                                {data.items.map(card => (
                                    // A (card + condition) can yield several tiles: one blank-copy
                                    // stack plus one per detailed copy — key on the copy for those.
                                    <OwnedConditionItem card={card} key={
                                        `${card.id}:${card.ownedGrade ?? ''}:` +
                                        (card.ownedCopies?.length === 1 ? card.ownedCopies[0].id : 'stack')
                                    } />
                                ))}
                            </div>
                        ) : (
                            <CardList cards={data.items} ownGrade={catalogGradeToCondition(params.grade)} />
                        )}
                        <AppPagination
                            metadata={data.pagination}
                            onPageChange={(page: number) => {
                                dispatch(setPageNumber(page));
                                window.scrollTo({ top: 0, behavior: 'smooth' });
                            }}
                        />
                    </>
                ) : (
                    <p className="est-note">{emptyHint}</p>
                )}
            </div>
        </div>
    );
}
