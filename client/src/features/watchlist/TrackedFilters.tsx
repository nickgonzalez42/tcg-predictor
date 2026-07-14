import { useEffect, useState } from "react";
import type { CardParams } from "../../app/models/cardParams";
import { useFetchFiltersQuery } from "../catalog/catalogApi";
import { createTrackedParamsSlice } from "./trackedParamsSlice";
import { useAppDispatch } from "../../app/store/store";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";
import MultiSelectDropdown from "../../app/shared/components/MultiSelectDropdown";
import { PRICE_TIER_OPTIONS } from "./grades";
import { GAMES as gameOptions } from "../../lib/games";

type Actions = ReturnType<typeof createTrackedParamsSlice>['actions'];

type Props = {
    params: CardParams
    actions: Actions            // the owning tracked slice's action creators
    sortGroups: { label: string; options: { value: string; label: string }[] }[]
}

// The catalog filter dropdown, ported for the tracked lists (watchlist /
// portfolio): same toggle + full-screen overlay, wired to a tracked params
// slice instead of the catalog's. Always in dropdown mode — these pages are
// full-width tables with no room for a rail.
export default function TrackedFilters({ params, actions, sortGroups }: Props) {
    const dispatch = useAppDispatch();
    // Set/rarity vocabularies are game-level metadata, shared with the catalog.
    const { data: filtersData } = useFetchFiltersQuery(params.game);

    const [open, setOpen] = useState(false);
    useEffect(() => {
        document.body.classList.toggle('filters-open', open);
        return () => document.body.classList.remove('filters-open');
    }, [open]);

    const search = useDebouncedSearch(params.searchTerm ?? '',
        v => dispatch(actions.setSearchTerm(v)));

    return (
        <div className="filters filters--dropdown">
            <button
                className="btn btn--outline filters__toggle"
                aria-expanded={open}
                onClick={() => setOpen(o => !o)}
            >
                Filters {open ? '▴' : '▾'}
            </button>
            <div className={`filters__body grid-box${open ? ' filters__body--open' : ''}`}>
            <div className="filters__head">
                <span className="filters__head-title">Filters</span>
                <button className="btn btn--outline" title="Close filters"
                    onClick={() => setOpen(false)}>✕</button>
            </div>
            <div className="filters__panels">
                <div className="panels__column">
                    <div className="panel">
                        <div className="field" style={{ margin: 0 }}>
                            <label htmlFor="tracked-search">Search cards</label>
                            <input
                                id="tracked-search"
                                className="input"
                                type="search"
                                value={search.term}
                                onChange={e => search.onChange(e.target.value)}
                            />
                        </div>
                    </div>
                    <div className="panel">
                        <label htmlFor="tracked-grade" className="field-label">Price shown</label>
                        <select
                            id="tracked-grade"
                            className="input"
                            value={params.grade ?? ''}
                            onChange={e => dispatch(actions.setGrade(e.target.value))}
                        >
                            {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                    </div>
                    <div className="panel">
                        <RadioButtonGroup
                            selectedValue={params.game}
                            options={gameOptions}
                            onChange={e => dispatch(actions.setGame(e.target.value))}
                        />
                    </div>
                </div>
                <div className="panels__column">
                    <div className="panel">
                        <label htmlFor="tracked-sort" className="field-label">Sort by</label>
                        <select id="tracked-sort" className="input" value={params.orderBy}
                            onChange={e => dispatch(actions.setOrderBy(e.target.value))}>
                            {sortGroups.map(g => (
                                <optgroup key={g.label} label={g.label}>
                                    {g.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                </optgroup>
                            ))}
                        </select>
                    </div>
                    <div className="panel">
                        <CheckBoxButtons
                            items={filtersData?.rarities ?? []}
                            checked={params.rarities}
                            onChange={(items: string[]) => dispatch(actions.setRarities(items))}
                        />
                    </div>
                    <div className="panel">
                        <MultiSelectDropdown
                            label="Sets"
                            items={filtersData?.sets ?? []}
                            checked={params.sets}
                            onChange={(items: string[]) => dispatch(actions.setSets(items))}
                        />
                    </div>
                </div>
            
            </div>
            
            {/* Footer: Reset + Apply, pinned to the bottom of the overlay.
                Filters apply live, so Apply just closes it. */}
            <div className="filters__footer">
                <button className="btn btn--outline" onClick={() => dispatch(actions.resetParams())}>
                    Reset filters
                </button>
                <button className="btn filters__apply" onClick={() => setOpen(false)}>
                    Apply
                </button>
            </div>
            </div>
        </div>
    );
}
