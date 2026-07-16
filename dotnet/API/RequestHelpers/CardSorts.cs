namespace API.RequestHelpers;

// A screener sort keyed on ACTUAL past price movement over a trend window.
// Metric is "pct" or "usd".
public sealed record HistorySort(string Metric, string Window, bool Descending);

// A screener sort keyed on the MODEL's expected change for a forecast horizon.
// Metric is "pct" or "usd".
public sealed record ForecastSort(string Metric, string Horizon, bool Descending);

// The catalog's orderBy vocabulary. Sorts that rank on market movement can't
// run in SQL (prices and forecasts live in other databases), so callers parse
// the key here and branch to an in-memory path when one matches.
public static class CardSorts
{
    public static bool IsPriceSort(string? orderBy) => orderBy is "price" or "priceDesc";

    // hist{Pct|Usd}{1w|1m|6m|1y}[Desc]
    public static HistorySort? History(string? orderBy) => orderBy switch
    {
        "histPct1w" => new("pct", "1w", false),  "histPct1wDesc" => new("pct", "1w", true),
        "histPct1m" => new("pct", "1m", false),  "histPct1mDesc" => new("pct", "1m", true),
        "histPct6m" => new("pct", "6m", false),  "histPct6mDesc" => new("pct", "6m", true),
        "histPct1y" => new("pct", "1y", false),  "histPct1yDesc" => new("pct", "1y", true),
        "histUsd1w" => new("usd", "1w", false),  "histUsd1wDesc" => new("usd", "1w", true),
        "histUsd1m" => new("usd", "1m", false),  "histUsd1mDesc" => new("usd", "1m", true),
        "histUsd6m" => new("usd", "6m", false),  "histUsd6mDesc" => new("usd", "6m", true),
        "histUsd1y" => new("usd", "1y", false),  "histUsd1yDesc" => new("usd", "1y", true),
        _ => null,
    };

    // chg{Pct|Usd}{1w|1m|6|12}[Desc] (legacy keys: month horizons drop the "m")
    public static ForecastSort? Forecast(string? orderBy) => orderBy switch
    {
        "chgPct1w" => new("pct", "1w", false),  "chgPct1wDesc" => new("pct", "1w", true),
        "chgPct1m" => new("pct", "1m", false),  "chgPct1mDesc" => new("pct", "1m", true),
        "chgPct6" => new("pct", "6m", false),   "chgPct6Desc" => new("pct", "6m", true),
        "chgPct12" => new("pct", "12m", false), "chgPct12Desc" => new("pct", "12m", true),
        "chgUsd1w" => new("usd", "1w", false),  "chgUsd1wDesc" => new("usd", "1w", true),
        "chgUsd1m" => new("usd", "1m", false),  "chgUsd1mDesc" => new("usd", "1m", true),
        "chgUsd6" => new("usd", "6m", false),   "chgUsd6Desc" => new("usd", "6m", true),
        "chgUsd12" => new("usd", "12m", false), "chgUsd12Desc" => new("usd", "12m", true),
        _ => null,
    };
}
