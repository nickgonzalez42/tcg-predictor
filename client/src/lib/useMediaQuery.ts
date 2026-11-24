import { useCallback, useSyncExternalStore } from "react";

// Reactive matchMedia: re-renders when the viewport crosses the query.
export function useMediaQuery(query: string) {
    const subscribe = useCallback((onChange: () => void) => {
        const mql = window.matchMedia(query);
        mql.addEventListener('change', onChange);
        return () => mql.removeEventListener('change', onChange);
    }, [query]);
    return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches);
}
