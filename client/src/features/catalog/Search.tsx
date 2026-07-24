import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { setSearchTerm } from "./catalogSlice";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";

export default function Search() {
    const { searchTerm } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();
    const { term, onChange } = useDebouncedSearch(searchTerm ?? '',
        v => dispatch(setSearchTerm(v)));

    return (
        <div className="field" style={{ margin: 0 }}>
            <label htmlFor="search">Search cards</label>
            <input
                id="search"
                className="input"
                type="search"
                value={term}
                onChange={e => onChange(e.target.value)}
            />
        </div>
    )
}
