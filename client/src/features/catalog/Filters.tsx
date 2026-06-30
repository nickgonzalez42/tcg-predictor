import Search from "./Search";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { resetParams, setGame, setOrderBy, setRarities, setSets } from "./catalogSlice";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";

const sortOptions = [
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: High to low' },
    { value: 'price', label: 'Price: Low to high' },
]

const gameOptions = [
    { value: 'onepiece', label: 'One Piece' },
    { value: 'pokemon', label: 'Pokémon' },
]

type Props = {
    filtersData: { sets: string[], rarities: string[] }
}

export default function Filters({ filtersData: data }: Props) {
    const { game, orderBy, sets, rarities } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();

    return (
        <div className="filters">
            <div className="panel">
                <Search />
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
                <CheckBoxButtons
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
