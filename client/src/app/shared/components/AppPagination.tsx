import type { Pagination as PaginationType } from "../../models/pagination";

type Props = {
    metadata: PaginationType
    onPageChange: (page: number) => void
}

export default function AppPagination({ metadata, onPageChange }: Props) {
    const { currentPage, totalPages, pageSize, totalCount } = metadata;
    const startItem = (currentPage - 1) * pageSize + 1;
    const endItem = Math.min(currentPage * pageSize, totalCount);

    return (
        <div className="pagination">
            <span className="text-muted">
                Displaying {startItem}-{endItem} of {totalCount} Items
            </span>
            <div className="pagination__pages">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map(page => (
                    <button
                        key={page}
                        className={`page-btn ${page === currentPage ? 'active' : ''}`}
                        onClick={() => onPageChange(page)}
                    >
                        {page}
                    </button>
                ))}
            </div>
        </div>
    )
}
