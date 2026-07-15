// Clickable, sortable column header. `k` is the ascending sort key; the
// descending variant is `${k}Desc`. Numeric columns default to desc-first,
// text columns pass ascFirst. Arrow shows on hover (see tables.css .sortable).
export default function SortTh({ label, k, orderBy, onSort, ascFirst = false, className = '' }: {
    label: React.ReactNode; k: string; orderBy: string;
    onSort: (v: string) => void; ascFirst?: boolean; className?: string;
}) {
    const desc = `${k}Desc`;
    const active = orderBy === k ? 'asc' : orderBy === desc ? 'desc' : null;
    const next = active === null ? (ascFirst ? k : desc) : active === 'desc' ? k : desc;
    return (
        <th className={`sortable${active ? ' sortable--active' : ''} ${className}`}
            aria-sort={active === 'asc' ? 'ascending' : active === 'desc' ? 'descending' : undefined}
            title={`Sort by ${typeof label === 'string' ? label.toLowerCase() : 'this column'}`}
            onClick={() => onSort(next)}>
            {label}
            <span className="sortable__arrow" aria-hidden="true">
                {(active ?? (next === k ? 'asc' : 'desc')) === 'asc' ? '▲' : '▼'}
            </span>
        </th>
    );
}
