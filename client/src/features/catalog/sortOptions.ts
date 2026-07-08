// Forecast sort options shared by the catalog and the tracked lists. Values are
// parsed server-side: chg{Pct|Usd}{6|12}[Desc]. Sorting by one of these also swaps
// the card's price for the expected change.
export const forecastSortOptions = [
    { value: 'chgPct12Desc', label: '12mo % change: high → low' },
    { value: 'chgPct12', label: '12mo % change: low → high' },
    { value: 'chgUsd12Desc', label: '12mo $ change: high → low' },
    { value: 'chgUsd12', label: '12mo $ change: low → high' },
    { value: 'chgPct6Desc', label: '6mo % change: high → low' },
    { value: 'chgPct6', label: '6mo % change: low → high' },
    { value: 'chgUsd6Desc', label: '6mo $ change: high → low' },
    { value: 'chgUsd6', label: '6mo $ change: low → high' },
];
