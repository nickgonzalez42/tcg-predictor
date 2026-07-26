import { useState } from "react";
import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { setSearchTerm } from "./catalogSlice";
import { useDebouncedSearch } from "../../lib/useDebouncedSearch";
import ImageSearchModal from "./ImageSearchModal";

export default function Search() {
    const { searchTerm } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();
    const { term, onChange } = useDebouncedSearch(searchTerm ?? '',
        v => dispatch(setSearchTerm(v)));
    const [showImageSearch, setShowImageSearch] = useState(false);

    return (
        <div className="field" style={{ margin: 0 }}>
            <label htmlFor="search">Search cards</label>
            <div className="search-row">
                <input
                    id="search"
                    className="input"
                    type="search"
                    placeholder="Search"
                    value={term}
                    onChange={e => onChange(e.target.value)}
                />
                <button className="btn btn--outline search-row__photo" title="Search by photo"
                    aria-label="Search by photo" onClick={() => setShowImageSearch(true)}>
                    📷
                </button>
            </div>
            {showImageSearch && <ImageSearchModal onClose={() => setShowImageSearch(false)} />}
        </div>
    )
}
