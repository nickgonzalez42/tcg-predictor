import Search from "./Search";
import RadioButtonGroup from "../../app/shared/components/RadioButtonGroup";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { resetParams, setBrands, setOrderBy, setTypes } from "./catalogSlice";
import CheckBoxButtons from "../../app/shared/components/CheckBoxButtons";

const sortOptions = [
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: High to low' },
    { value: 'price', label: 'Price: Low to high' },
]

type Props = {
    filtersData: { brands: string[], types: string[] }
}

export default function Filters({ filtersData: data }: Props) {
    const { orderBy, types, brands } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();

    return (
        <div className="filters">
            <div className="panel">
                <Search />
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
                    items={data.brands}
                    checked={brands}
                    onChange={(items: string[]) => dispatch(setBrands(items))}
                />
            </div>
            <div className="panel">
                <CheckBoxButtons
                    items={data.types}
                    checked={types}
                    onChange={(items: string[]) => dispatch(setTypes(items))}
                />
            </div>
            <button className="btn btn--outline" onClick={() => dispatch(resetParams())}>
                Reset filters
            </button>
        </div>
    )
}
