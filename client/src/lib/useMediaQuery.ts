import { useEffect, useState } from "react";

// Reactive matchMedia: re-renders when the viewport crosses the query.
export function useMediaQuery(query: string) {
    const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
    useEffect(() => {
        const mql = window.matchMedia(query);
        const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
        mql.addEventListener('change', onChange);
        setMatches(mql.matches);
        return () => mql.removeEventListener('change', onChange);
    }, [query]);
    return matches;
}
