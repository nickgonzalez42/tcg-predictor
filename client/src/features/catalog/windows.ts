import { trendForSort, type SortGroup } from "./sortOptions";

// Which trend windows a game can serve a forecast for (2026-11-05, user: "if
// the game is missing any model predictions, show those aren't available").
// The model publishes per game and horizon, and a game whose history is too
// short for a horizon has no rows there at all: the four TCGplayer-only games
// carried 1M only until their first monthly retrain, and a brand-new game has
// nothing until its first night. The catalog then disables that window's chip
// and hides its sorts, and the active sort steps down to a window the game
// has, coming back on its own once the facets say the rows exist (see the
// slice's fitWindows and the orderWish / trendWish fields).
export type Window = '1m' | '6m' | '1y';
export type Windows = Record<Window, boolean>;
export const ALL_WINDOWS: Windows = { '1m': true, '6m': true, '1y': true };
export const WINDOW_LABEL: Record<Window, string> = { '1m': '1-month', '6m': '6-month', '1y': '1-year' };

// The facet fields that decide it (GET cards/filters).
export type FacetWindows = { hasYear?: boolean; forecastHorizons?: string[]; hasEarly?: boolean };

// EARLY rows are 1-month estimates, so they open the 1M window by themselves
// (a game shows early calls at 1M before its model trains). 1Y needs the 12m
// model AND a year of price history (hasYear, as before). Facets from an API
// without forecastHorizons, or none yet, count every window as served.
export function windowsFor(f?: FacetWindows | null): Windows {
    if (!f?.forecastHorizons) return { ...ALL_WINDOWS, '1y': f?.hasYear ?? true };
    const h = new Set(f.forecastHorizons);
    return {
        '1m': h.has('1m') || !!f.hasEarly,
        '6m': h.has('6m'),
        '1y': (f.hasYear ?? true) && h.has('12m'),
    };
}

// Longest first: a sort the game can't serve steps DOWN to the next window
// it has (1Y -> 6M -> 1M), never up.
const ORDER: Window[] = ['1y', '6m', '1m'];

// The window a forecast or past-change sort is tied to; null for the general
// sorts (name, price) and the 1w window, which every game serves.
export function windowOf(orderBy: string): Window | null {
    const w = trendForSort(orderBy);
    return w === '1m' || w === '6m' || w === '1y' ? w : null;
}

export function snapSort(orderBy: string, w: Windows): string {
    const win = windowOf(orderBy);
    if (!win || w[win]) return orderBy;
    const to = ORDER.slice(ORDER.indexOf(win) + 1).find(x => w[x]);
    if (!to) return 'priceDesc';   // no forecast window at all: fall back to price
    const fc = /^(chg(?:Pct|Usd))(?:1m|6|12)(Desc)?$/.exec(orderBy);
    if (fc) return `${fc[1]}${{ '1m': '1m', '6m': '6', '1y': '12' }[to]}${fc[2] ?? ''}`;
    const past = /^(hist(?:Pct|Usd))(?:1m|6m|1y)(Desc)?$/.exec(orderBy);
    if (past) return `${past[1]}${to}${past[2] ?? ''}`;
    return orderBy;
}

export function snapTrend(trend: string | undefined, w: Windows): string | undefined {
    if (trend !== '1m' && trend !== '6m' && trend !== '1y') return trend;   // 1w or unset: untouched
    if (w[trend]) return trend;
    return ORDER.slice(ORDER.indexOf(trend) + 1).find(x => w[x]) ?? '1m';
}

// The sort menu without the windows this game can't serve.
export function withoutUnavailableSorts(groups: SortGroup[], w: Windows): SortGroup[] {
    const ok = (v: string) => { const win = windowOf(v); return !win || w[win]; };
    return groups.map(g => ({ ...g, options: g.options.filter(o => ok(o.value)) }));
}

const joinList = (xs: string[]) =>
    xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} or ${xs[xs.length - 1]}`;

// What the filter rail says about the missing windows, in the collector's
// words. `landedOrder` is the sort the catalog stepped down to when the
// visitor's own choice could not be served; null when nothing is missing.
export function missingWindowsNote(gameLabel: string, f: FacetWindows | null | undefined, landedOrder?: string): string | null {
    const w = windowsFor(f);
    const missing = (['1m', '6m', '1y'] as Window[]).filter(x => !w[x]).map(x => WINDOW_LABEL[x]);
    if (missing.length === 0) return null;
    const list = joinList(missing);
    const earlyOnly = f?.forecastHorizons?.length === 0 && !!f.hasEarly;
    const head = earlyOnly
        ? `${gameLabel} has early estimates only, so there are no ${list} calls yet.`
        : `No ${list} calls for ${gameLabel} yet.`;
    if (!landedOrder) return head;
    const landed = windowOf(landedOrder);
    return landed
        ? `${head} Showing ${WINDOW_LABEL[landed]} calls until they arrive.`
        : `${head} Sorted by price until they arrive.`;
}
