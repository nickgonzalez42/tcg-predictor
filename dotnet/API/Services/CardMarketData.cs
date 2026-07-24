using API.Data;
using API.DTOS;
using API.RequestHelpers;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// A card's expected move for one (forecast target, horizon): the % and $
// change plus the from (current) and to (forecast) prices.
public readonly record struct ForecastChange(double Pct, double Usd, double From, double To);

// A card's actual price movement over one trend window. No value floor: the
// catalog's default $10 minimum-price filter is what keeps penny-card noise
// out of the default view, so the ranking itself stays honest.
public readonly record struct HistoryChange(double Pct, double Usd);

// Market context for card DTOs. Cards live in per-game DBs, price history in
// pricecharting.db, and model forecasts in predictions.db — this service owns
// every lookup that joins them and the decoration the tiles/screener rows use.
public class CardMarketData(PredictionsContext predictions, PriceChartingContext priceCharting)
{
    // One row per trend window: when the window starts, and which trained
    // forecast horizon a tile's headline forecast should use. History points
    // are monthly, so short windows read as "the last known price N ago".
    private static readonly Dictionary<string, (Func<DateTime> Start, string FcstHorizon)> TrendWindows = new()
    {
        ["1w"] = (() => DateTime.UtcNow.AddDays(-7), "1w"),
        ["1m"] = (() => DateTime.UtcNow.AddMonths(-1), "1m"),
        ["6m"] = (() => DateTime.UtcNow.AddMonths(-6), "6m"),
        ["1y"] = (() => DateTime.UtcNow.AddYears(-1), "12m"),
    };

    // Public: the movers cache keys on the same normalization, so unknown
    // trend values collapse onto their effective entry.
    public static string NormalizeTrend(string? trend) =>
        trend != null && TrendWindows.ContainsKey(trend.ToLowerInvariant()) ? trend.ToLowerInvariant() : "1m";

    private static string WindowStart(string window) =>
        TrendWindows[window].Start().ToString("yyyy-MM-dd");

    // Latest (most recent date) price per product at one condition tier.
    public async Task<Dictionary<int, double>> LatestTierPrices(string game, string? grade, List<int> ids)
    {
        if (ids.Count == 0) return [];
        var tier = GradeTiers.PriceTier(grade);

        var rows = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();

        return rows
            .GroupBy(r => r.ProductId)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(r => r.Date).First().Price);
    }

    // Products with a current price at the tier, from the snapshot table (one
    // row per card, so this is a single cheap scan).
    public async Task<HashSet<int>> TierPricedIds(string game, string tier)
    {
        var q = priceCharting.GradedPrices.Where(x => x.Game == game);
        q = tier switch
        {
            "grade7" => q.Where(x => x.Grade7 != null),
            "grade8" => q.Where(x => x.Grade8 != null),
            "grade9" => q.Where(x => x.Grade9 != null),
            "grade95" => q.Where(x => x.Grade95 != null),
            "psa10" => q.Where(x => x.Psa10 != null),
            "bgs10" => q.Where(x => x.Bgs10 != null),
            "cgc10" => q.Where(x => x.Cgc10 != null),
            "sgc10" => q.Where(x => x.Sgc10 != null),
            _ => q.Where(x => x.Ungraded != null),
        };
        return (await q.Select(x => x.ProductId).ToListAsync()).ToHashSet();
    }

    // Actual price movement per product over one trend window, on the tier's
    // history. The anchor is the last point at-or-before the window start
    // (the price as it stood back then), else the series' first point — the
    // same rule the tiles' trend pill uses, so orderings agree with displays.
    public async Task<Dictionary<int, HistoryChange>> HistoryChanges(
        string game, string tier, List<int> ids, string window)
    {
        if (ids.Count == 0) return [];
        var cutoff = WindowStart(window);

        var rows = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();

        var changes = new Dictionary<int, HistoryChange>();
        foreach (var g in rows.GroupBy(r => r.ProductId))
        {
            var series = g.OrderBy(r => r.Date).ToList();
            var latest = series[^1];
            var anchor = series.LastOrDefault(r => string.CompareOrdinal(r.Date, cutoff) <= 0)
                         ?? series[0];
            if (anchor.Price <= 0) continue;
            changes[g.Key] = new HistoryChange(
                (latest.Price / anchor.Price - 1) * 100, latest.Price - anchor.Price);
        }
        return changes;
    }

    // Expected model change per product for one (target, horizon).
    public async Task<Dictionary<int, ForecastChange>> ForecastChanges(
        string game, string target, string horizon, List<int> ids)
    {
        if (ids.Count == 0) return [];
        var rows = await predictions.Forecasts
            .Where(f => f.Game == game && f.Target == target && f.Horizon == horizon
                        && f.BasePrice > 0 && ids.Contains(f.ProductId))
            .Select(f => new { f.ProductId, f.BasePrice, f.ForecastPrice })
            .ToListAsync();
        return rows.ToDictionary(r => r.ProductId, r => new ForecastChange(
            Pct: r.BasePrice > 0 ? (r.ForecastPrice / r.BasePrice - 1) * 100 : 0.0,
            Usd: r.ForecastPrice - r.BasePrice,
            From: r.BasePrice,
            To: r.ForecastPrice));
    }

    // Fill the "expected change" screener columns from a forecast-sorted query.
    public static void ApplyExpected(CardDto card, ForecastChange change, ForecastSort sort)
    {
        card.ExpectedChange = sort.Metric == "pct" ? change.Pct : change.Usd;
        card.ExpectedUnit = sort.Metric == "pct" ? "percent" : "usd";
        card.ExpectedHorizon = sort.Horizon;
        card.ExpectedFrom = change.From;
        card.ExpectedTo = change.To;
    }

    // When a grade/condition tier is explicitly selected, override the headline
    // with that tier's latest price (same source as the forecast "Current").
    // The default Near Mint price is the near_mint_price column, set in ToDto.
    public async Task ApplyGradePrice(List<CardDto> cards, string game, string? grade)
    {
        if (string.IsNullOrEmpty(grade) || cards.Count == 0) return;

        var latest = await LatestTierPrices(game, grade, cards.Select(c => c.Id).ToList());
        foreach (var card in cards)
            card.Price = latest.TryGetValue(card.Id, out var p) ? p : null;
    }

    // Lightweight per-card market context for tiles / screener rows: a sparkline
    // and price movement over ONE shared trend window (so the graph and the
    // "$from → $to" figures always agree), plus the 6m/12m forecast changes.
    // Everything is computed for the SHOWN condition tier (Near Mint when none is
    // selected; conditions without their own forecast, like LP/MP, fall back to
    // the ungraded forecast). One history + one forecast query per page.
    public async Task ApplyMarket(
        List<CardDto> cards, string game, string? grade = null, string? trend = null, string? fcstOverride = null)
    {
        if (cards.Count == 0) return;
        var ids = cards.Select(c => c.Id).Distinct().ToList();
        var tier = GradeTiers.PriceTier(grade ?? "");
        var target = GradeTiers.ForecastTarget(grade);
        // Played conditions (LP/MP) have their own price history but no forecasts;
        // decorating an LP price with the Near Mint forecast would be misleading,
        // so those tiers show history only.
        var tierHasForecast = tier == "ungraded" || GradeTiers.Graded.Contains(tier);
        var period = NormalizeTrend(trend);
        var window = TrendWindows[period];
        var cutoff = WindowStart(period);

        var hist = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();
        var seriesById = hist
            .GroupBy(h => h.ProductId)
            .ToDictionary(g => g.Key, g => g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList());

        // 6m/12m always load for the screener columns; the tile's headline
        // forecast follows the window's mapped horizon unless overridden
        // (movers rank on 1m but keep the year-long sparkline window — a 1m
        // window would leave monthly history with too few points to draw).
        var fcstHorizon = fcstOverride ?? window.FcstHorizon;
        var horizons = new[] { "6m", "12m", fcstHorizon }.Distinct().ToArray();
        var fc = tierHasForecast
            ? await predictions.Forecasts
                .Where(f => f.Game == game && f.Target == target && horizons.Contains(f.Horizon)
                            && ids.Contains(f.ProductId))
                .Select(f => new { f.ProductId, f.Horizon, f.BasePrice, f.ForecastPrice, f.Confidence })
                .ToListAsync()
            : [];
        var fcById = fc.ToLookup(f => f.ProductId);

        foreach (var card in cards)
        {
            if (seriesById.TryGetValue(card.Id, out var series))
            {
                card.HistoryMonths = series.Count;   // full depth, for confidence
                card.PriceAsOf = series[^1].Date;    // freshness of the shown price

                // Span = the anchor (last point at-or-before the window start,
                // i.e. the price as it stood back then) + everything after it.
                var anchorIdx = series.FindLastIndex(p => string.CompareOrdinal(p.Date, cutoff) <= 0);
                var span = series.Skip(Math.Max(anchorIdx, 0)).ToList();
                if (span.Count > 0)
                {
                    // No new point inside the window means the price hasn't moved
                    // (carry-forward) — draw that as a flat line, not an empty one.
                    card.Sparkline = span.Count == 1
                        ? [span[0].Price, span[0].Price]
                        : span.Select(p => p.Price).ToList();
                    card.TrendPct = span[0].Price > 0
                        ? (span[^1].Price / span[0].Price - 1) * 100
                        : null;
                    card.TrendPeriod = period;
                }
            }
            foreach (var f in fcById[card.Id])
            {
                var pct = f.BasePrice > 0 ? (f.ForecastPrice / f.BasePrice - 1) * 100 : 0;
                if (f.Horizon == "6m") card.Fcst6Pct = pct;
                else if (f.Horizon == "12m")
                {
                    card.Fcst12Pct = pct;
                    card.Fcst12To = f.ForecastPrice;
                }
                if (f.Horizon == fcstHorizon)
                {
                    card.FcstTo = f.ForecastPrice;
                    card.FcstHorizon = fcstHorizon;
                    card.FcstConfidence = f.Confidence;
                }
            }
        }
    }
}
