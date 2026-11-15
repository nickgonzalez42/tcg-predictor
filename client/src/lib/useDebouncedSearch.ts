import { useEffect, useRef, useState } from "react";

// Local input state that commits to the store 500ms after the user stops
// typing, and follows external resets (e.g. game switches clearing the term).
export function useDebouncedSearch(value: string, onCommit: (v: string) => void) {
    const [term, setTerm] = useState(value);
    const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

    // Following the store is done while rendering, not in an effect: a reset
    // lands in the same render instead of one stale frame and a second pass
    // (https://react.dev/learn/you-might-not-need-an-effect).
    const [followed, setFollowed] = useState(value);
    if (value !== followed) {
        setFollowed(value);
        setTerm(value);
    }

    // A pending commit must not fire after unmount — it would silently change
    // store state (e.g. catalog params) behind the next page's back.
    useEffect(() => () => clearTimeout(timer.current), []);

    const onChange = (v: string) => {
        setTerm(v);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => onCommit(v), 500);
    };

    // Commit right away, skipping the pause (Enter, a clear button). A value
    // the store already holds isn't sent again, so it can't reset the page.
    const commit = (v: string) => {
        clearTimeout(timer.current);
        setTerm(v);
        if (v !== value) onCommit(v);
    };

    return { term, onChange, commit };
}
