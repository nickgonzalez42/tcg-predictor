// Forecast sort options shared by the catalog and the tracked lists. Values are
// parsed server-side: chg{Pct|Usd}{1w|1m|6|12}[Desc]. Sorting by one of these
// also swaps the card's price for the expected change, and on the catalog it
// snaps the trend-window chips to the matching period (see catalogSlice).
export const forecastSortOptions = [
    { value: 'chgPct12Desc', label: '1 year, % gain, high to low' },
    { value: 'chgPct12', label: '1 year, % gain, low to high' },
    { value: 'chgUsd12Desc', label: '1 year, $ gain, high to low' },
    { value: 'chgUsd12', label: '1 year, $ gain, low to high' },
    { value: 'chgPct6Desc', label: '6 months, % gain, high to low' },
    { value: 'chgPct6', label: '6 months, % gain, low to high' },
    { value: 'chgUsd6Desc', label: '6 months, $ gain, high to low' },
    { value: 'chgUsd6', label: '6 months, $ gain, low to high' },
    { value: 'chgPct1mDesc', label: '1 month, % gain, high to low' },
    { value: 'chgPct1m', label: '1 month, % gain, low to high' },
    { value: 'chgUsd1mDesc', label: '1 month, $ gain, high to low' },
    { value: 'chgUsd1m', label: '1 month, $ gain, low to high' },
];

// PAST price growth over a trend window, matching the tiles' PAST pill.
// Values are parsed server-side: hist{Pct|Usd}{1w|1m|6m|1y}[Desc].
export const historySortOptions = [
    { value: 'histPct1yDesc', label: 'Past year, % change, high to low' },
    { value: 'histPct1y', label: 'Past year, % change, low to high' },
    { value: 'histUsd1yDesc', label: 'Past year, $ change, high to low' },
    { value: 'histUsd1y', label: 'Past year, $ change, low to high' },
    { value: 'histPct6mDesc', label: 'Past 6 months, % change, high to low' },
    { value: 'histPct6m', label: 'Past 6 months, % change, low to high' },
    { value: 'histUsd6mDesc', label: 'Past 6 months, $ change, high to low' },
    { value: 'histUsd6m', label: 'Past 6 months, $ change, low to high' },
    { value: 'histPct1mDesc', label: 'Past month, % change, high to low' },
    { value: 'histPct1m', label: 'Past month, % change, low to high' },
    { value: 'histUsd1mDesc', label: 'Past month, $ change, high to low' },
    { value: 'histUsd1m', label: 'Past month, $ change, low to high' },
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
            { value: 'name', label: 'Name, A to Z' },
            { value: 'priceDesc', label: 'Price, high to low' },
            { value: 'price', label: 'Price, low to high' },
        ],
    },
    { label: 'Our forecast', options: forecastSortOptions },
    { label: 'What already happened', options: historySortOptions },
];

// Pre-release mode (2026-10-10): the predicted launch price is the only
// number these cards have, so it is the only thing to sort on.
export const prereleaseSortGroups: SortGroup[] = [
    {
        label: 'Pre-release',
        options: [
            { value: 'priceDesc', label: 'Predicted price, high to low' },
            { value: 'price', label: 'Predicted price, low to high' },
            { value: 'name', label: 'Name, A to Z' },
        ],
    },
];
export const PRERELEASE_SORTS = prereleaseSortGroups[0].options.map(o => o.value);

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

// 1Y sort values (12m forecast growth / 1y past growth) — hidden for games
// without year-deep data (young games: no 12m horizon, <12 months history).
export const isYearSort = (v: string) => /^(chg(?:Pct|Usd)12|hist(?:Pct|Usd)1y)/.test(v);

export function withoutYearSorts(groups: SortGroup[]): SortGroup[] {
    return groups.map(g => ({ ...g, options: g.options.filter(o => !isYearSort(o.value)) }));
}

// The 6M equivalent of a 1Y sort (used to snap the selection when switching
// to a game that has no 1Y data).
export function yearSortTo6m(v: string): string {
    return v.replace(/^(chg(?:Pct|Usd))12/, '$16').replace(/^(hist(?:Pct|Usd))1y/, '$16m');
}

// The forecast horizon each trend window maps to (mirrors the API's
// TrendWindows: the model has no 1y horizon, so 1Y shows the 12m forecast).
export const TREND_FCST: Record<string, string> = { '1m': '1M', '6m': '6M', '1y': '1Y' };
