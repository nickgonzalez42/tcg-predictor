export type CardParams = {
    game: string;
    orderBy: string;
    searchTerm?: string;
    sets: string[];
    rarities: string[];
    grade?: string;
    minPrice?: string;    // range on the shown price; '' = unset
    maxPrice?: string;
    pageNumber: number;
    pageSize: number;
    trend?: string;       // trend window for sparkline/movement: 1w|1m|6m|1y
    view?: CatalogView;   // client-only presentation state (never sent to the API)
    gameInitialized?: boolean;  // client-only: the default game has been decided
                                // (URL param, user choice, or portfolio majority)
}

export type CatalogView = 'cards' | 'rows';
