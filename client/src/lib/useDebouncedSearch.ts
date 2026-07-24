import { useEffect, useRef, useState } from "react";

// Local input state that commits to the store 500ms after the user stops
// typing, and follows external resets (e.g. game switches clearing the term).
export function useDebouncedSearch(value: string, onCommit: (v: string) => void) {
    const [term, setTerm] = useState(value);
    const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

    useEffect(() => { setTerm(value); }, [value]);

    // A pending commit must not fire after unmount — it would silently change
    // store state (e.g. catalog params) behind the next page's back.
    useEffect(() => () => clearTimeout(timer.current), []);

    const onChange = (v: string) => {
        setTerm(v);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => onCommit(v), 500);
    };

    return { term, onChange };
}
