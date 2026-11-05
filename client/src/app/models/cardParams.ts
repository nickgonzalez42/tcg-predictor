export type CardParams = {
    game: string;
    orderBy: string;
    searchTerm?: string;
    sets: string[];
    rarities: string[];
    confidence: string[];  // forecast confidence levels to keep: high|med|low
    printings: string[];   // keep only cards carrying one of these printings
    grade?: string;
    minPrice?: string;    // range on the shown price; '' = unset
    maxPrice?: string;
    pageNumber: number;
    pageSize: number;
    trend?: string;       // trend window for sparkline/movement: 1w|1m|6m|1y
    market?: string;      // cards shown: '' = with market price (default),
                          // 'without' = predicted-price cards only, 'all' = both
                          // (sent to the API only when non-empty)
    view?: CatalogView;   // client-only presentation state (never sent to the API)
    quickAdd?: boolean;   // client-only: catalog quick-add mode (one-click +1 on every card)
    gameInitialized?: boolean;  // client-only: the default game has been decided
                                // (URL param, user choice, or portfolio majority)
    setsDecided?: 'default' | 'user';  // client-only: how the set filter was settled —
                                       // 'default' = the newest set applied for the
                                       // visitor, 'user' = their own pick or clear;
                                       // unset = waiting on the game's facets
    orderWish?: string;   // client-only: the sort asked for while the game can't serve
                          // its window (orderBy stepped down to one it can); fitWindows
                          // restores it once the facets say the rows exist
    trendWish?: string;   // client-only: the same for the trend window
}

export type CatalogView = 'cards' | 'rows';
