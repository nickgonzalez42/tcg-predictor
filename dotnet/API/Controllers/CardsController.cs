using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Catalog endpoints. The tracked-list endpoints (owned/wishlist) live in
// CardsController.Tracked.cs; cross-database market lookups in CardMarketData;
// the movers ranking in MoverService.
public partial class CardsController(
    CardSources sources,
    PredictionsContext predictions, PriceChartingContext priceCharting,
    StoreContext store, ReasoningService reasoning,
    CardMarketData market, MoverService movers) : BaseApiController
{
    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        var game = GameRegistry.KeyOrDefault(cardParams.Game);
        return await Page(sources.Cards(game).VisibleInCatalog(), cardParams, game);
    }

    [HttpGet("{game}/{id:int}")]
    public async Task<ActionResult<CardDto>> GetCard(string game, int id)
    {
        var folder = GameRegistry.KeyOrDefault(game);

        // Art-pending cards are stored but not served (a direct link 404s too).
        var card = await sources.Find(folder, id);
        var dto = string.IsNullOrEmpty(card?.ImagePath)
            ? null
            : card!.ToDto(folder, CardImageUrl(folder, card.Id));

        if (dto == null) return NotFound();

        dto.GradedPrices = await GetGradedPrices(folder, id);

        await market.ApplyMarket([dto], folder);   // PriceAsOf + market context for the header
        return dto;   // headline price is the near_mint_price column, set in ToDto
    }

    // Current PriceCharting graded/ungraded prices for a single card (detail view).
    private async Task<GradedPriceDto?> GetGradedPrices(string game, int id)
    {
        var g = await priceCharting.GradedPrices
            .FirstOrDefaultAsync(x => x.Game == game && x.ProductId == id);
        if (g == null) return null;

        return new GradedPriceDto
        {
            Ungraded = g.Ungraded,
            Grade7 = g.Grade7,
            Grade8 = g.Grade8,
            Grade9 = g.Grade9,
            Grade95 = g.Grade95,
            Psa10 = g.Psa10,
            Bgs10 = g.Bgs10,
            Cgc10 = g.Cgc10,
            Sgc10 = g.Sgc10,
            SalesVolume = g.SalesVolume,
            UpdatedAt = g.UpdatedAt,
        };
    }

    [HttpGet("filters")]
    public async Task<IActionResult> GetFilters([FromQuery] string? game)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var (sets, rarities) = await Facets(sources.Cards(key).VisibleInCatalog());

        // 1Y views only make sense once the game has year-deep data: a 12m
        // forecast horizon, or 12+ months of price history. Young games
        // (PriceCharting picked up digimon/gundam in 2025-09) have neither,
        // so the client disables the 1Y trend chip and hides 1Y sorts.
        var hasYear = await predictions.Forecasts
            .AnyAsync(f => f.Game == key && f.Horizon == "12m");
        if (!hasYear)
        {
            var yearAgo = DateTime.UtcNow.AddMonths(-12).ToString("yyyy-MM-dd");
            hasYear = await priceCharting.History
                .AnyAsync(h => h.Game == key && string.Compare(h.Date, yearAgo) <= 0);
        }

        return Ok(new { sets, rarities, hasYear });
    }

    // Monthly price history per condition tier, for charting (TradingView-style).
    [HttpGet("{game}/{id:int}/history")]
    public async Task<IActionResult> GetHistory(string game, int id, [FromQuery] string? grade)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var query = priceCharting.History.Where(h => h.Game == key && h.ProductId == id);
        if (!string.IsNullOrEmpty(grade)) query = query.Where(h => h.Grade == grade);

        var points = await query.OrderBy(h => h.Date).ToListAsync();
        var series = points
            .GroupBy(p => p.Grade)
            .ToDictionary(g => g.Key, g => g.Select(p => new { p.Date, p.Price, p.Source }).ToList());

        return Ok(new { game = key, productId = id, series });
    }

    // LLM-written plain-English "take" summarizing the forecast (cached; null when
    // no Anthropic key is configured or the card has no forecast).
    [HttpGet("{game}/{id:int}/reasoning")]
    public async Task<IActionResult> GetReasoning(string game, int id)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var card = await sources.Find(key, id);
        var prose = await reasoning.GetAsync(key, id, card?.Name, card?.SetName);
        return Ok(new { game = key, productId = id, prose });
    }

    // Model price forecasts (1m/6m/12m per condition tier) with confidence bands.
    [HttpGet("{game}/{id:int}/forecast")]
    public async Task<IActionResult> GetForecast(string game, int id)
    {
        var key = GameRegistry.KeyOrDefault(game);
        // The site serves 1m/6m/12m only. 1w rows are still generated and
        // archived by the pipeline (baseline for the future weekly model) but
        // never leave the API.
        var rows = await predictions.Forecasts
            .Where(f => f.Game == key && f.ProductId == id && f.Horizon != "1w")
            .ToListAsync();

        // Months of history per tier — a proxy for how trustworthy the forecast is.
        var monthsByTier = await priceCharting.History
            .Where(h => h.Game == key && h.ProductId == id)
            .GroupBy(h => h.Grade)
            .Select(g => new { Grade = g.Key, Months = g.Count() })
            .ToDictionaryAsync(x => x.Grade, x => x.Months);

        var forecasts = rows.Select(f => new
        {
            f.Target, f.Horizon,
            // Display date: the REAL date of the anchor price when the pipeline
            // recorded it; AsOf (its month bucket, stamped the 1st) as fallback.
            AsOf = f.AnchorDate ?? f.AsOf,
            f.BasePrice,
            f.ForecastPrice, f.Low, f.High, f.Ret, f.Reason, f.Confidence,
            Months = monthsByTier.GetValueOrDefault(f.Target, 0),
        });

        return Ok(new { game = key, productId = id, forecasts });
    }

    // Past forecasts whose horizon has elapsed, for drawing "what the model
    // said back then" on the chart. Target dates mirror the pipeline's
    // scorecard: 1w counts from issue time, month horizons from the anchoring
    // price month. The archive starts 2026-07-09, so points accumulate from
    // one horizon-length after that.
    [HttpGet("{game}/{id:int}/forecast-history")]
    public async Task<IActionResult> GetForecastHistory(string game, int id)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var rows = await predictions.ForecastArchive
            .Where(f => f.Game == key && f.ProductId == id && f.ForecastPrice != null
                        && f.Horizon != "1w")   // site serves 1m/6m/12m only
            .ToListAsync();

        var today = DateTime.UtcNow.Date;
        var past = rows
            .Select(f => new { f, TargetDate = ForecastTargetDate(f) })
            .Where(x => x.TargetDate != null && x.TargetDate <= today)
            .Select(x => new
            {
                x.f.Target,
                x.f.Horizon,
                TargetDate = x.TargetDate!.Value.ToString("yyyy-MM-dd"),
                x.f.ForecastPrice,
                x.f.Low,
                x.f.High,
                x.f.BasePrice,
                x.f.AsOf,
                x.f.ScoredAt,
                x.f.RealizedPrice,
            })
            .OrderBy(x => x.TargetDate)
            .ToList();

        return Ok(new { game = key, productId = id, forecasts = past });
    }

    private static readonly Dictionary<string, int> HorizonMonths =
        new() { ["1m"] = 1, ["6m"] = 6, ["12m"] = 12 };

    private static DateTime? ForecastTargetDate(ArchivedForecast f)
    {
        if (f.Horizon == "1w")
            return DateTime.TryParse(f.ScoredAt, out var issued) ? issued.Date.AddDays(7) : null;
        if (HorizonMonths.TryGetValue(f.Horizon, out var months)
            && DateTime.TryParse(f.AsOf, out var asOf))
            return asOf.Date.AddMonths(months);
        return null;
    }

    // Summary stats per tier (current, all-time high/low, % change windows).
    [HttpGet("{game}/{id:int}/stats")]
    public async Task<IActionResult> GetStats(string game, int id)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var points = await priceCharting.History
            .Where(h => h.Game == key && h.ProductId == id)
            .OrderBy(h => h.Date).ToListAsync();

        var grades = points
            .GroupBy(p => p.Grade)
            .ToDictionary(g => g.Key, g => StatsFor(g.ToList()));

        var current = await priceCharting.GradedPrices
            .FirstOrDefaultAsync(x => x.Game == key && x.ProductId == id);

        return Ok(new { game = key, productId = id, salesVolume = current?.SalesVolume, grades });
    }

    private static object StatsFor(List<PriceHistoryPoint> series)
    {
        var latest = series[^1];
        var latestDate = DateTime.Parse(latest.Date);

        double? changeOver(int months)
        {
            // dates are yyyy-MM-dd, so ordinal string comparison avoids
            // re-parsing every point for every window
            var target = latestDate.AddMonths(-months).ToString("yyyy-MM-dd");
            var past = series.LastOrDefault(p => string.CompareOrdinal(p.Date, target) <= 0);
            return past == null || past.Price == 0
                ? null
                : Math.Round((latest.Price / past.Price - 1) * 100, 1);
        }

        return new
        {
            current = latest.Price,
            asOf = latest.Date,
            ath = series.Max(p => p.Price),
            atl = series.Min(p => p.Price),
            change1m = changeOver(1),
            change6m = changeOver(6),
            change1y = changeOver(12),
            change5y = changeOver(60),
            points = series.Count,
        };
    }

    // Top movers across the games by ungraded forecast change — feeds the
    // market ticker and the home page tiles.
    [HttpGet("movers")]
    public async Task<IActionResult> GetMovers([FromQuery] int count = 12, [FromQuery] string? horizon = null)
    {
        return Ok(await movers.TopMovers(count, horizon, CardImageUrl));
    }

    // ----- Catalog paging -----
    // The default path sorts and paginates in SQL. Sorts or filters that key on
    // data in another database (tier prices, history, forecasts) pull the
    // filtered cards into memory and use PageSlice instead.

    private async Task<List<CardDto>> Page(
        IQueryable<CardBase> source, CardParams cardParams, string folder)
    {
        var filtered = source
            .Search(cardParams.SearchTerm)
            .Filter(cardParams.Sets, cardParams.Rarities);

        // A selected tier REFILTERS the catalog: only cards actually priced at
        // that tier are listed (no '—' rows). Tier prices live in another
        // DbContext, so this is an id-set filter.
        var tier = GradeTiers.PriceTier(cardParams.Grade ?? "");
        if (tier != "ungraded")
        {
            var priced = await market.TierPricedIds(folder, tier);
            filtered = filtered.Where(c => priced.Contains(c.Id));
        }

        // Min/max on the SHOWN price. With no tier selected that's the Near Mint
        // column (filterable in SQL); a selected tier's price lives in another
        // DbContext, so those paths filter in memory below.
        if (string.IsNullOrEmpty(cardParams.Grade))
            filtered = filtered.PriceRange(cardParams.MinPrice, cardParams.MaxPrice);

        if (CardSorts.History(cardParams.OrderBy) is { } historySort)
            return await PageByHistory(filtered, cardParams, folder, historySort);

        if (CardSorts.Forecast(cardParams.OrderBy) is { } forecastSort)
            return await PageByForecast(filtered, cardParams, folder, forecastSort);

        // When a specific grade tier is shown AND the sort or range filter keys on
        // its price — which lives in a different DbContext (priceCharting) — it
        // can't be handled in SQL alongside the card query. Sort/filter/paginate
        // in memory instead, so the displayed price and the rows agree.
        if (!string.IsNullOrEmpty(cardParams.Grade)
            && (CardSorts.IsPriceSort(cardParams.OrderBy) || HasPriceRange(cardParams)))
            return await PageByGradePrice(filtered, cardParams, folder);

        var query = filtered.Sort(cardParams.OrderBy);
        var paged = await PagedList<CardBase>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = ToDtos(paged, folder);
        await market.ApplyGradePrice(cards, folder, cardParams.Grade);
        await market.ApplyMarket(cards, folder, cardParams.Grade, cardParams.Trend);
        return cards;
    }

    // Sort + paginate the filtered set by an expected forecast change (cross-DB, so
    // in memory). Cards without a forecast sort to the end and keep showing their price.
    private async Task<List<CardDto>> PageByForecast(
        IQueryable<CardBase> filtered, CardParams p, string folder, ForecastSort sort)
    {
        var all = await filtered.ToListAsync();
        all = await FilterByShownPrice(all, p, folder);

        var changes = await market.ForecastChanges(
            folder, GradeTiers.ForecastTarget(p.Grade), sort.Horizon, all.Select(c => c.Id).ToList());
        double Key(ForecastChange ch) => sort.Metric == "pct" ? ch.Pct : ch.Usd;

        var withFc = all.Where(c => changes.ContainsKey(c.Id));
        var without = all.Where(c => !changes.ContainsKey(c.Id));
        var sorted = (sort.Descending
                ? withFc.OrderByDescending(c => Key(changes[c.Id]))
                : withFc.OrderBy(c => Key(changes[c.Id])))
            .Concat(without)
            .ToList();

        var cards = ToDtos(PageSlice(sorted, p), folder);

        await market.ApplyGradePrice(cards, folder, p.Grade);
        foreach (var card in cards)
            if (changes.TryGetValue(card.Id, out var ch)) CardMarketData.ApplyExpected(card, ch, sort);
        await market.ApplyMarket(cards, folder, p.Grade, p.Trend);
        return cards;
    }

    // Sort + paginate by ACTUAL price growth over one trend window, computed on
    // the shown tier's history — the same anchor rule the tiles' PAST pill uses,
    // so the row order always agrees with the displayed movement.
    private async Task<List<CardDto>> PageByHistory(
        IQueryable<CardBase> filtered, CardParams p, string folder, HistorySort sort)
    {
        var all = await filtered.ToListAsync();
        all = await FilterByShownPrice(all, p, folder);

        var tier = GradeTiers.PriceTier(p.Grade ?? "");
        var changes = await market.HistoryChanges(folder, tier, all.Select(c => c.Id).ToList(), sort.Window);

        // % ranking floors penny cards (Pct == null): they cluster after every
        // $5+ card so rounding noise can't top the list, but still order by
        // their real move (RawPct) so later pages stay sorted. A $ move can't
        // be faked by rounding, so every card with history ranks normally.
        double? Key(CardBase c) => changes.TryGetValue(c.Id, out var ch)
            ? (sort.Metric == "pct" ? ch.Pct : ch.Usd)
            : null;
        var withChg = all.Where(c => Key(c) != null);
        var floored = all.Where(c => Key(c) == null && changes.ContainsKey(c.Id));
        var noHistory = all.Where(c => !changes.ContainsKey(c.Id));
        var sorted = (sort.Descending
                ? withChg.OrderByDescending(c => Key(c))
                : withChg.OrderBy(c => Key(c)))
            .Concat(sort.Descending
                ? floored.OrderByDescending(c => changes[c.Id].RawPct)
                : floored.OrderBy(c => changes[c.Id].RawPct))
            .Concat(noHistory)
            .ToList();

        var cards = ToDtos(PageSlice(sorted, p), folder);

        await market.ApplyGradePrice(cards, folder, p.Grade);
        await market.ApplyMarket(cards, folder, p.Grade, sort.Window);   // tiles trend over the sorted window
        return cards;
    }

    // Sort + paginate the full filtered set by a selected grade tier's price (cross-DB,
    // so in memory). Cards with no price for the tier sort to the end (in both
    // directions), matching their '—' display.
    private async Task<List<CardDto>> PageByGradePrice(
        IQueryable<CardBase> filtered, CardParams p, string folder)
    {
        var all = await filtered.ToListAsync();
        var prices = await market.LatestTierPrices(folder, p.Grade, all.Select(c => c.Id).ToList());

        var priced = all.Where(c => prices.ContainsKey(c.Id) && InPriceRange(prices[c.Id], p));
        // Cards with no price for the tier normally list at the end (matching
        // their '—' display) — but never inside an explicit price range.
        var unpriced = HasPriceRange(p)
            ? Enumerable.Empty<CardBase>()
            : all.Where(c => !prices.ContainsKey(c.Id));
        var sorted = (p.OrderBy switch
            {
                "priceDesc" => priced.OrderByDescending(c => prices[c.Id]),
                "price" => priced.OrderBy(c => prices[c.Id]),
                // routed here by the range filter with a non-price sort
                _ => priced.OrderBy(c => c.Name),
            })
            .Concat(unpriced)
            .ToList();

        var cards = ToDtos(PageSlice(sorted, p), folder);

        foreach (var card in cards)
            card.Price = prices.TryGetValue(card.Id, out var v) ? v : null;
        await market.ApplyMarket(cards, folder, p.Grade, p.Trend);
        return cards;
    }

    // Min/max on a selected tier's shown price. The ungraded case was already
    // range-filtered in SQL before the in-memory paths run.
    private async Task<List<CardBase>> FilterByShownPrice(List<CardBase> all, CardParams p, string folder)
    {
        if (string.IsNullOrEmpty(p.Grade) || !HasPriceRange(p)) return all;
        var shown = await market.LatestTierPrices(folder, p.Grade, all.Select(c => c.Id).ToList());
        return all.Where(c => shown.TryGetValue(c.Id, out var v) && InPriceRange(v, p)).ToList();
    }

    private static bool HasPriceRange(CardParams p) => p.MinPrice != null || p.MaxPrice != null;

    private static bool InPriceRange(double v, CardParams p) =>
        (p.MinPrice is not { } min || v >= min) && (p.MaxPrice is not { } max || v <= max);

    // Emit the pagination header and slice one page from an already-sorted list —
    // the in-memory counterpart of PagedList, for sorts that span databases.
    private List<T> PageSlice<T>(List<T> sorted, CardParams p)
    {
        Response.AddPaginationHeader(PaginationMetadata.For(sorted.Count, p.PageNumber, p.PageSize));
        return sorted.Skip((p.PageNumber - 1) * p.PageSize).Take(p.PageSize).ToList();
    }

    private List<CardDto> ToDtos(IEnumerable<CardBase> cards, string folder) =>
        cards.Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id))).ToList();

    private static async Task<(List<string> Sets, List<string> Rarities)> Facets<T>(
        IQueryable<T> source) where T : CardBase
    {
        var sets = await source.Where(x => x.SetName != null)
            .Select(x => x.SetName!).Distinct().OrderBy(x => x).ToListAsync();
        var rarities = await source.Where(x => x.Rarity != null)
            .Select(x => x.Rarity!).Distinct().OrderBy(x => x).ToListAsync();

        return (sets, rarities);
    }
}
