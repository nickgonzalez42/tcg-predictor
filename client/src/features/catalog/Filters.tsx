import Search from "./Search";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { resetParams, setGame, setGrade, setOrderBy, setRarities, setSets } from "./catalogSlice";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";
import MultiSelectDropdown from "../../app/shared/components/MultiSelectDropdown";
import { forecastSortOptions } from "./sortOptions";
import { PRICE_TIER_OPTIONS } from "../watchlist/grades";

const sortOptions = [
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: desc' },
    { value: 'price', label: 'Price: asc' },
    ...forecastSortOptions,
]

import { GAMES as gameOptions } from "../../lib/games";


type Props = {
    filtersData: { sets: string[], rarities: string[] }
}

export default function Filters({ filtersData: data }: Props) {
    const { game, orderBy, sets, rarities, grade } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();

    return (
        <div className="filters">
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
                    {sortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
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
            <button className="btn btn--outline" onClick={() => dispatch(resetParams())}>
                Reset filters
            </button>
        </div>
    )
}
