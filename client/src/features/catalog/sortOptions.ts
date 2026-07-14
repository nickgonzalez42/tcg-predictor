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
// Values are parsed server-side: hist{Pct|Usd}{1w|1m|6m|1y}[Desc].
export const historySortOptions = [
    { value: 'histPct1yDesc', label: '1Y % growth: desc' },
    { value: 'histPct1y', label: '1Y % growth: asc' },
    { value: 'histUsd1yDesc', label: '1Y $ growth: desc' },
    { value: 'histUsd1y', label: '1Y $ growth: asc' },
    { value: 'histPct6mDesc', label: '6M % growth: desc' },
    { value: 'histPct6m', label: '6M % growth: asc' },
    { value: 'histUsd6mDesc', label: '6M $ growth: desc' },
    { value: 'histUsd6m', label: '6M $ growth: asc' },
    { value: 'histPct1mDesc', label: '1M % growth: desc' },
    { value: 'histPct1m', label: '1M % growth: asc' },
    { value: 'histUsd1mDesc', label: '1M $ growth: desc' },
    { value: 'histUsd1m', label: '1M $ growth: asc' },
    { value: 'histPct1wDesc', label: '1W % growth: desc' },
    { value: 'histPct1w', label: '1W % growth: asc' },
    { value: 'histUsd1wDesc', label: '1W $ growth: desc' },
    { value: 'histUsd1w', label: '1W $ growth: asc' },
];

// Trend window the chips should snap to when a forecast or history sort is
// chosen (sort horizons and history windows share vocabulary except 12m -> 1y).
export function trendForSort(orderBy: string): string | null {
    const h = /^hist(?:Pct|Usd)(1w|1m|6m|1y)(?:Desc)?$/.exec(orderBy);
    if (h) return h[1];
    const m = /^chg(?:Pct|Usd)(1w|1m|6|12)(?:Desc)?$/.exec(orderBy);
    if (!m) return null;
    return { '1w': '1w', '1m': '1m', '6': '6m', '12': '1y' }[m[1]] ?? null;
}

type SortOption = { value: string; label: string };
type SortGroup = { label: string; options: SortOption[] };

// Catalog sort menu, grouped: general fields, model forecast growth, actual
// past growth. Shared so the tracked lists match it.
export const catalogSortGroups: SortGroup[] = [
    {
        label: 'General',
        options: [
            { value: 'name', label: 'Alphabetical' },
            { value: 'priceDesc', label: 'Price: desc' },
            { value: 'price', label: 'Price: asc' },
        ],
    },
    { label: 'Forecast growth', options: forecastSortOptions },
    { label: 'Past growth', options: historySortOptions },
];

// Tracked lists (Portfolio / Wishlist): the same groups as the catalog, plus
// the list-specific "Recently added" default at the top.
export const trackedSortGroups: SortGroup[] = [
    {
        label: 'General',
        options: [
            { value: '', label: 'Recently added' },
            ...catalogSortGroups[0].options,
        ],
    },
    ...catalogSortGroups.slice(1),
];
