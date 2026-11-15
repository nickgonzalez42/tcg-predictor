import type { ReactNode } from "react";

// Stale-while-fetching, made visible (2026-11-13). When a list's filters,
// sort or page change, RTK Query keeps the old rows on screen until the new
// ones arrive (isFetching with no currentData yet). This dims them and runs a
// thin line along the top edge, so nobody reads the old list as the answer
// to what they just picked. The first load still gets the CardLoader.
// className carries the layout (e.g. "subgrid full-span" inside the catalog
// grid); the rows stay clickable throughout.
export default function UpdatingList({ updating, className, children }: {
    updating: boolean; className?: string; children: ReactNode;
}) {
    return (
        <div className={`updating-list${className ? ` ${className}` : ''}${updating ? ' updating-list--on' : ''}`}
            aria-busy={updating || undefined}>
            <span className="updating-list__line" aria-hidden="true" />
            {children}
        </div>
    );
}
