// Forecast sort options shared by the catalog and the tracked lists. Values are
// parsed server-side: chg{Pct|Usd}{1w|1m|6|12}[Desc]. Sorting by one of these
// also swaps the card's price for the expected change, and on the catalog it
// snaps the trend-window chips to the matching period (see catalogSlice).
export const forecastSortOptions = [
    { value: 'chgPct12Desc', label: '1Y % growth: desc' },
    { value: 'chgPct12', label: '1Y % growth: asc' },
    { value: 'chgUsd12Desc', label: '1Y $ growth: desc' },
    { value: 'chgUsd12', label: '1Y $ growth: asc' },
    { value: 'chgPct6Desc', label: '6M % growth: desc' },
    { value: 'chgPct6', label: '6M % growth: asc' },
    { value: 'chgUsd6Desc', label: '6M $ growth: desc' },
    { value: 'chgUsd6', label: '6M $ growth: asc' },
    { value: 'chgPct1mDesc', label: '1M % growth: desc' },
    { value: 'chgPct1m', label: '1M % growth: asc' },
    { value: 'chgUsd1mDesc', label: '1M $ growth: desc' },
    { value: 'chgUsd1m', label: '1M $ growth: asc' },
    { value: 'chgPct1wDesc', label: '1W % growth: desc' },
    { value: 'chgPct1w', label: '1W % growth: asc' },
    { value: 'chgUsd1wDesc', label: '1W $ growth: desc' },
    { value: 'chgUsd1w', label: '1W $ growth: asc' },
];

// PAST price growth over a trend window, matching the tiles' PAST pill.
// Values are parsed server-side: hist{1w|1m|6m|1y}[Desc].
export const historySortOptions = [
    { value: 'hist1yDesc', label: '1Y % growth: desc' },
    { value: 'hist1y', label: '1Y % growth: asc' },
    { value: 'hist6mDesc', label: '6M % growth: desc' },
    { value: 'hist6m', label: '6M % growth: asc' },
    { value: 'hist1mDesc', label: '1M % growth: desc' },
    { value: 'hist1m', label: '1M % growth: asc' },
    { value: 'hist1wDesc', label: '1W % growth: desc' },
    { value: 'hist1w', label: '1W % growth: asc' },
];

// Trend window the chips should snap to when a forecast or history sort is
// chosen (sort horizons and history windows share vocabulary except 12m -> 1y).
export function trendForSort(orderBy: string): string | null {
    const h = /^hist(1w|1m|6m|1y)(?:Desc)?$/.exec(orderBy);
    if (h) return h[1];
    const m = /^chg(?:Pct|Usd)(1w|1m|6|12)(?:Desc)?$/.exec(orderBy);
    if (!m) return null;
    return { '1w': '1w', '1m': '1m', '6': '6m', '12': '1y' }[m[1]] ?? null;
}

// Sort menu for the tracked lists (Portfolio / Wishlist).
export const trackedSortOptions = [
    { value: '', label: 'Recently added' },
    { value: 'name', label: 'Alphabetical' },
    { value: 'priceDesc', label: 'Price: desc' },
    { value: 'price', label: 'Price: asc' },
    ...forecastSortOptions,
];
