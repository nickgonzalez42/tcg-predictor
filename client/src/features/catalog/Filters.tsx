import Search from "./Search";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { resetParams, setGame, setGrade, setOrderBy, setRarities, setSets } from "./catalogSlice";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";
import MultiSelectDropdown from "../../app/shared/components/MultiSelectDropdown";

const sortOptions = [
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: High to low' },
    { value: 'price', label: 'Price: Low to high' },
]

const gameOptions = [
    { value: 'onepiece', label: 'One Piece' },
    { value: 'pokemon', label: 'Pokémon' },
]

// Which tier's price to show. '' = default TCGplayer market price.
const gradeOptions = [
    { value: '', label: 'Market price' },
    { value: 'lp', label: 'Lightly Played' },
    { value: 'mp', label: 'Moderately Played' },
    { value: 'grade7', label: 'Grade 7' },
    { value: 'grade8', label: 'Grade 8' },
    { value: 'grade9', label: 'Grade 9' },
    { value: 'grade95', label: 'Grade 9.5' },
    { value: 'psa10', label: 'PSA 10' },
]

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
                    {gradeOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
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
                <RadioButtonGroup
                    selectedValue={orderBy}
                    options={sortOptions}
                    onChange={e => dispatch(setOrderBy(e.target.value))}
                />
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
