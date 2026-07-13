import { useEffect, useState } from "react";
import Search from "./Search";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { resetParams, setGame, setGrade, setMaxPrice, setMinPrice, setOrderBy, setRarities, setSets } from "./catalogSlice";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import { useMediaQuery } from "../../lib/useMediaQuery";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";
import MultiSelectDropdown from "../../app/shared/components/MultiSelectDropdown";
import { forecastSortOptions, historySortOptions } from "./sortOptions";
import { PRICE_TIER_OPTIONS } from "../watchlist/grades";

// Grouped sort menu: the optgroup labels disambiguate the forecast growth
// sorts (model predictions) from the past growth sorts (actual history).
const sortGroups = [
    {
        label: 'General',
        options: [
            { value: 'name', label: 'Alphabetical' },
            { value: 'priceDesc', label: 'Price: desc' },
            { value: 'price', label: 'Price: asc' },
        ],
    },
    { label: 'Forecast growth', options: forecastSortOptions },
    { label: 'Past growth', options: historySortOptions },
]

import { GAMES as gameOptions } from "../../lib/games";


type Props = {
    filtersData: { sets: string[], rarities: string[] }
}

export default function Filters({ filtersData: data }: Props) {
    const { game, orderBy, sets, rarities, grade, minPrice, maxPrice, view } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();

    // Dropdown mode: the panels collapse behind a full-width toggle — on
    // tablet/mobile always, and at any width in row view (the table wants the
    // whole grid). Open, they cover the whole screen; a body class locks the
    // page scroll behind the overlay.
    const isTablet = useMediaQuery('(max-width: 1023px)');
    const dropdown = isTablet || view === 'rows';
    const [open, setOpen] = useState(false);
    useEffect(() => {
        if (!dropdown) setOpen(false);   // leaving dropdown mode closes the overlay
    }, [dropdown]);
    useEffect(() => {
        document.body.classList.toggle('filters-open', open && dropdown);
        return () => document.body.classList.remove('filters-open');
    }, [open, dropdown]);

    // Debounced so each keystroke doesn't fire a query; commits follow store
    // resets (e.g. Reset filters) automatically. Six-figure ceiling.
    const MAX_PRICE_INPUT = 999999;
    const capPrice = (v: string) =>
        v !== '' && Number(v) > MAX_PRICE_INPUT ? String(MAX_PRICE_INPUT) : v;
    const min = useDebouncedSearch(minPrice ?? '', v => dispatch(setMinPrice(capPrice(v))));
    const max = useDebouncedSearch(maxPrice ?? '', v => dispatch(setMaxPrice(capPrice(v))));

    return (
        <div className={`filters${dropdown ? ' filters--dropdown' : ''}`}>
            <button
                className="btn btn--outline filters__toggle"
                aria-expanded={open}
                onClick={() => setOpen(o => !o)}
            >
                Filters {open ? '▴' : '▾'}
            </button>
            <div className={`filters__body${open ? ' filters__body--open' : ''}`}>
            {/* Overlay header: only rendered/visible inside the full-screen state. */}
            <div className="filters__head">
                <span className="filters__head-title">Filters</span>
                <button className="btn btn--outline" title="Close filters"
                    onClick={() => setOpen(false)}>✕</button>
            </div>
            <div className="filters__panels">
            <div className="panel">
                <Search />
            </div>
            <div className="panel">
                <label htmlFor="grade-select" className="field-label">Price shown</label>
                <select
                    id="grade-select"
                    className="input"
                    value={grade ?? ''}
                    onChange={e => dispatch(setGrade(e.target.value))}
                >
                    {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <div className="price-range">
                    <input
                        className="input" type="number" min="0" max="999999" step="any" inputMode="decimal"
                        placeholder="Min $" aria-label="Minimum shown price"
                        value={min.term} onChange={e => min.onChange(e.target.value)}
                    />
                    <span className="price-range__dash">–</span>
                    <input
                        className="input" type="number" min="0" max="999999" step="any" inputMode="decimal"
                        placeholder="Max $" aria-label="Maximum shown price"
                        value={max.term} onChange={e => max.onChange(e.target.value)}
                    />
                </div>
            </div>
            <div className="panel">
                <RadioButtonGroup
                    selectedValue={game}
                    options={gameOptions}
                    onChange={e => dispatch(setGame(e.target.value))}
                />
            </div>
            <div className="panel">
                <label htmlFor="sort-select" className="field-label">Sort by</label>
                <select id="sort-select" className="input" value={orderBy}
                    onChange={e => dispatch(setOrderBy(e.target.value))}>
                    {sortGroups.map(g => (
                        <optgroup key={g.label} label={g.label}>
                            {g.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                    ))}
                </select>
            </div>
            <div className="panel">
                <CheckBoxButtons
                    items={data.rarities}
                    checked={rarities}
                    onChange={(items: string[]) => dispatch(setRarities(items))}
                />
            </div>
            <div className="panel">
                <MultiSelectDropdown
                    label="Sets"
                    items={data.sets}
                    checked={sets}
                    onChange={(items: string[]) => dispatch(setSets(items))}
                />
            </div>
            </div>
            {/* Footer: Reset + (dropdown-only) Apply, pinned to the bottom of
                the overlay. Filters apply live, so Apply just closes it. */}
            <div className="filters__footer">
                <button className="btn btn--outline" onClick={() => dispatch(resetParams())}>
                    Reset filters
                </button>
                {dropdown && (
                    <button className="btn filters__apply" onClick={() => setOpen(false)}>
                        Apply
                    </button>
                )}
            </div>
            </div>
        </div>
    )
}
