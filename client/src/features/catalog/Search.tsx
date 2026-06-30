import { useAppDispatch, useAppSelector } from "../../app/store/store";
import { setSearchTerm } from "./catalogSlice";
import { useEffect, useRef, useState } from "react";

export default function Search() {
    const { searchTerm } = useAppSelector(state => state.catalog);
    const dispatch = useAppDispatch();
    const [term, setTerm] = useState(searchTerm);
    const timeout = useRef<ReturnType<typeof setTimeout>>(undefined);

    useEffect(() => {
        setTerm(searchTerm)
    }, [searchTerm]);

    const handleChange = (value: string) => {
        setTerm(value);
        if (timeout.current) clearTimeout(timeout.current);
        timeout.current = setTimeout(() => {
            dispatch(setSearchTerm(value));
        }, 500);
    };

    return (
        <div className="field" style={{ margin: 0 }}>
            <label htmlFor="search">Search cards</label>
            <input
                id="search"
                className="input"
                type="search"
                value={term}
                onChange={e => handleChange(e.target.value)}
            />
        </div>
    )
}
