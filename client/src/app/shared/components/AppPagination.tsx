import type { Pagination as PaginationType } from "../../models/pagination";

type Props = {
    metadata: PaginationType
    onPageChange: (page: number) => void
}

// Truncated page list: 1 2 3 … i-2 i-1 i … last  (with the last page kept so you
// can still jump to the end; gaps become an ellipsis).
function pageList(current: number, total: number): (number | '…')[] {
    const pages = new Set<number>();
    [1, 2, 3].forEach(p => pages.add(p));
    [current - 2, current - 1, current].forEach(p => pages.add(p));
    pages.add(total);

    const sorted = [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
    const out: (number | '…')[] = [];
    let prev = 0;
    for (const p of sorted) {
        if (p - prev > 1) out.push('…');
        out.push(p);
        prev = p;
    }
    return out;
}

// Changing pages always returns the reader to the top of the list.
export default function AppPagination({ metadata, onPageChange }: Props) {
    const changePage = (page: number) => {
        onPageChange(page);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };

    const { currentPage, totalPages, pageSize, totalCount } = metadata;
    const startItem = (currentPage - 1) * pageSize + 1;
    const endItem = Math.min(currentPage * pageSize, totalCount);

    return (
        <div className="pagination">
            <span className="text-muted">
                Displaying {startItem}-{endItem} of {totalCount} Items
            </span>
            <div className="pagination__pages">
                <button
                    className="page-btn"
                    disabled={currentPage <= 1}
                    onClick={() => changePage(currentPage - 1)}
                >
                    ‹
                </button>
                {pageList(currentPage, totalPages).map((p, i) =>
                    p === '…' ? (
                        <span key={`e${i}`} className="page-ellipsis">…</span>
                    ) : (
                        <button
                            key={p}
                            className={`page-btn ${p === currentPage ? 'active' : ''}`}
                            onClick={() => changePage(p)}
                        >
                            {p}
                        </button>
                    )
                )}
                <button
                    className="page-btn"
                    disabled={currentPage >= totalPages}
                    onClick={() => changePage(currentPage + 1)}
                >
                    ›
                </button>
            </div>
        </div>
    )
}
