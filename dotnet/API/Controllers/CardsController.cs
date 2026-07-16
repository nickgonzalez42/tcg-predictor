using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class CardsController(
    CardSources sources,
    PredictionsContext predictions, PriceChartingContext priceCharting,
    StoreContext store, ReasoningService reasoning, IConfiguration config) : BaseApiController
{
    // The home page / ticker showcase cards visually, so a mover must have its
    // scraped art on disk; cards whose image hasn't landed yet are skipped.
    private bool HasLocalImage(string game, int id)
    {
        var dir = config[$"CardImages:{game}"];
        if (string.IsNullOrWhiteSpace(dir)) return true;   // dirs unconfigured — don't blank the page
        return System.IO.File.Exists(Path.Combine(dir, $"{id}.jpg"));
    }

    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        var game = GameRegistry.KeyOrDefault(cardParams.Game);
        return await Page(sources.Cards(game).VisibleInCatalog(), cardParams, game);
    }

    // One of the signed-in user's tracked lists (owned | wishlist), with the same
    // search/filter/sort/pagination as the catalog. Default sort = order added.
    [Authorize]
    [HttpGet("tracked")]
    public async Task<ActionResult<List<CardDto>>> GetTracked([FromQuery] CardParams cardParams, [FromQuery] string? kind)
    {
        var listKind = TrackKind.Normalize(kind);
        var game = GameRegistry.KeyOrDefault(cardParams.Game);

        // Owned is shown one tile per (card + condition) with a quantity, so it has
        // its own paging path. Wishlist is one tile per card.
        if (listKind == TrackKind.Owned)
            return await PageOwnedByCondition(sources.Cards(game), cardParams, game);

        var user = User.Identity!.Name!;
        var tracked = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.Kind == listKind)
            .OrderByDescending(x => x.AddedAt)
            .Select(x => new { x.ProductId, x.WatchedAtPrice, x.AlertTargetPrice, x.AddedAt })
            .ToListAsync();
        var trackedIds = tracked.Select(x => x.ProductId).ToList();

        var cards = await PageScoped(sources.Cards(game), cardParams, game, trackedIds);

        // Wishlist rows carry their watch-time price and alert target. The
        // stored snapshot is the NM price at watch time; when a graded tier is
        // shown, "watched at" instead reads that tier's history at-or-before
        // the watch date, so the column (and its "since added" %) compares
        // like with like rather than a graded price against a raw snapshot.
        var byId = tracked.ToDictionary(x => x.ProductId);
        var tier = GradeTiers.PriceTier(cardParams.Grade);
        Dictionary<int, List<(string Date, double Price)>>? tierHist = null;
        if (tier != "ungraded" && cards.Count > 0)
        {
            var ids = cards.Select(c => c.Id).ToList();
            tierHist = (await priceCharting.History
                .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
                .Select(h => new { h.ProductId, h.Date, h.Price })
                .ToListAsync())
                .GroupBy(h => h.ProductId)
                .ToDictionary(g => g.Key,
                    g => g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList());
        }
        foreach (var card in cards)
            if (byId.TryGetValue(card.Id, out var t))
            {
                card.WatchedAtPrice = t.WatchedAtPrice;
                if (tierHist != null)
                {
                    // Last tier price at-or-before the watch date; none = no value.
                    var cutoff = t.AddedAt.ToString("yyyy-MM-dd");
                    double? at = null;
                    if (tierHist.TryGetValue(card.Id, out var series))
                        foreach (var p in series)
                        {
                            if (string.CompareOrdinal(p.Date, cutoff) > 0) break;
                            at = p.Price;
                        }
                    card.WatchedAtPrice = at;
                }
                card.WatchedSince = t.AddedAt;
                card.AlertTargetPrice = t.AlertTargetPrice;
            }

        return cards;
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

        await ApplyMarket([dto], folder);   // PriceAsOf + market context for the header
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

    // Model price forecasts (6m/12m for ungraded + PSA 10) with confidence bands.
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

    // Top movers across both games by ungraded forecast change — feeds the
    // market ticker and the home page mover tiles. Mixed horizons: 12m where a
    // game has it, else its longest available (6m for young games — digimon /
    // gundam have <14 months of history). Small floor price so penny cards'
    // huge percentages don't drown out everything.
    [HttpGet("movers")]
    public async Task<IActionResult> GetMovers([FromQuery] int count = 12, [FromQuery] string? horizon = null)
    {
        count = Math.Clamp(count, 1, 24);

        // horizon=mix (the homepage hero): one small slice per forecast
        // category, deduped — each card carries its own horizon's forecast
        // fields. The per-game guarantee is skipped; the client round-robins
        // games and the cross-category spread supplies the variety.
        if (horizon == "mix")
        {
            var mixed = new List<CardDto>();
            foreach (var h in new[] { "1m", "6m", "12m" })
                mixed.AddRange(await PickMovers(h, Math.Max(2, count / 3), guaranteeGames: false));
            return Ok(mixed.DistinctBy(m => (m.Game, m.Id)).ToList());
        }

        // Single ranking horizon (1m | 6m | 12m). The default 12m keeps the
        // legacy behavior where games without year-deep data fall back to 6m;
        // an explicit horizon applies to every game (all games train them).
        var hz = horizon is "1m" or "6m" ? horizon : "12m";
        return Ok(await PickMovers(hz, count, guaranteeGames: true));
    }

    private async Task<List<CardDto>> PickMovers(string hz, int count, bool guaranteeGames)
    {
        var gamesWith12m = (await predictions.Forecasts
            .Where(f => f.Target == "ungraded" && f.Horizon == "12m")
            .Select(f => f.Game).Distinct().ToListAsync()).ToHashSet();

        var moverPool = predictions.Forecasts
            .Where(f => f.Target == "ungraded" && f.BasePrice >= 10);
        moverPool = hz == "12m"
            ? moverPool.Where(f => f.Horizon == "12m"
                                   || (f.Horizon == "6m" && !gamesWith12m.Contains(f.Game)))
            : moverPool.Where(f => f.Horizon == hz);
        var baseQuery = moverPool
            .Select(f => new { f.Game, f.ProductId, f.BasePrice, f.ForecastPrice });
        // 2x buffer per side: candidates without local art are filtered below.
        var gainers = await baseQuery.OrderByDescending(f => f.ForecastPrice / f.BasePrice).Take(count * 2).ToListAsync();
        var losers = await baseQuery.OrderBy(f => f.ForecastPrice / f.BasePrice).Take(count * 2).ToListAsync();

        // Balanced mix, alternating up/down: ordering purely by |change| lets the
        // triple-digit gainers crowd every loser out of the ticker. Only genuine
        // movers qualify for each side; if one side runs dry the other fills in.
        var ups = gainers.Where(f => f.ForecastPrice > f.BasePrice);
        var downs = losers.Where(f => f.ForecastPrice < f.BasePrice);
        var globalPicks = ups.Select((f, i) => (f, rank: i * 2))
            .Concat(downs.Select((f, i) => (f, rank: i * 2 + 1)))
            .OrderBy(x => x.rank)
            .Select(x => x.f)
            .DistinctBy(f => (f.Game, f.ProductId))
            .Where(f => HasLocalImage(f.Game, f.ProductId))
            .ToList();

        // Every game with forecasts is GUARANTEED two movers (its strongest
        // gainer + strongest loser) so no game monopolizes the showcase; the
        // remaining slots come from the global ranking. Small buffers per side
        // because art-less candidates are skipped.
        var guaranteed = new List<MoverPick>();
        foreach (var game in guaranteeGames ? GameRegistry.Keys : Array.Empty<string>())
        {
            // Progressive price floor: prefer $10+ movers, but a game whose whole
            // ungraded market sits below that (e.g. Digimon) still gets its two.
            var gameHorizon = hz != "12m" ? hz : (gamesWith12m.Contains(game) ? "12m" : "6m");
            foreach (var gainerSide in new[] { true, false })
            {
                MoverPick? pick = null;
                foreach (var floor in new[] { 10.0, 1.0 })
                {
                    var q = predictions.Forecasts
                        .Where(f => f.Game == game && f.Target == "ungraded" && f.Horizon == gameHorizon
                                    && f.BasePrice >= floor)
                        .Where(f => gainerSide ? f.ForecastPrice > f.BasePrice : f.ForecastPrice < f.BasePrice);
                    q = gainerSide
                        ? q.OrderByDescending(f => f.ForecastPrice / f.BasePrice)
                        : q.OrderBy(f => f.ForecastPrice / f.BasePrice);
                    var hit = (await q.Take(5).ToListAsync())
                        .FirstOrDefault(f => HasLocalImage(f.Game, f.ProductId));
                    if (hit != null)
                    {
                        pick = new MoverPick(hit.Game, hit.ProductId, hit.BasePrice, hit.ForecastPrice);
                        break;
                    }
                }
                if (pick != null) guaranteed.Add(pick);
            }
        }

        var picked = guaranteed
            .Concat(globalPicks.Select(f => new MoverPick(f.Game, f.ProductId, f.BasePrice, f.ForecastPrice)))
            .DistinctBy(f => (f.Game, f.ProductId))
            .Take(Math.Max(count, guaranteed.Count))
            .ToList();

        // Join card names/sets from the right game DB, in memory (cross-DB).
        var byGame = new Dictionary<string, Dictionary<int, CardBase>>();
        foreach (var game in GameRegistry.Keys)
        {
            var ids = picked.Where(f => f.Game == game).Select(f => f.ProductId).ToList();
            byGame[game] = ids.Count == 0
                ? []
                : (await sources.Cards(game).Where(c => ids.Contains(c.Id)).ToListAsync())
                    .ToDictionary(c => c.Id, c => c);
        }

        var movers = picked
            .Select(f =>
            {
                var card = byGame.GetValueOrDefault(f.Game)?.GetValueOrDefault(f.ProductId);
                if (card == null) return null;
                // the normal DTO mapping keeps movers identical to catalog cards
                // (CardType etc.); ApplyMarket below fills the forecast fields
                var dto = card.ToDto(f.Game, CardImageUrl(f.Game, card.Id));
                dto.Game = f.Game;
                dto.Price ??= f.BasePrice;
                return dto;
            })
            .OfType<CardDto>()
            .ToList();

        // Sparkline/trend always use the year window (6m for young games) so the
        // tiles have enough monthly points to draw; the headline forecast fields
        // (FcstTo/FcstHorizon) follow the requested ranking horizon.
        foreach (var game in GameRegistry.Keys)
            await ApplyMarket(movers.Where(m => m.Game == game).ToList(), game,
                trend: gamesWith12m.Contains(game) ? "1y" : "6m",
                fcstOverride: hz == "12m" ? null : hz);

        return movers;
    }

    private sealed record MoverPick(string Game, int ProductId, double BasePrice, double ForecastPrice);

    // Owned-copy condition vocabulary, worst-to-best, for the Condition header
    // sort ('' = unspecified sorts first, top slabs last).
    private static readonly string[] ConditionOrder =
        ["", "mp", "lp", "nm", "grade7", "grade8", "grade9", "grade95",
         "psa10", "bgs10", "cgc10", "sgc10"];

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

    private static bool HasPriceRange(CardParams p) => p.MinPrice != null || p.MaxPrice != null;

    private static bool InPriceRange(double v, CardParams p) =>
        (p.MinPrice is not { } min || v >= min) && (p.MaxPrice is not { } max || v <= max);

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
            var priced = await TierPricedIds(folder, tier);
            filtered = filtered.Where(c => priced.Contains(c.Id));
        }

        // Min/max on the SHOWN price. With no tier selected that's the Near Mint
        // column (filterable in SQL); a selected tier's price lives in another
        // DbContext, so those paths filter in memory below.
        if (string.IsNullOrEmpty(cardParams.Grade))
            filtered = filtered.PriceRange(cardParams.MinPrice, cardParams.MaxPrice);

        // Sorting by PAST price growth over a trend window (cross-DB: history
        // lives in pricecharting.db, so it's an in-memory path like forecasts).
        if (ParseHistorySort(cardParams.OrderBy) is { } hs)
            return await PageByHistory(filtered, cardParams, folder, hs.metric, hs.window, hs.desc);

        // Sorting by an expected forecast change (cross-DB: forecasts live in predictions.db).
        if (ParseForecastSort(cardParams.OrderBy) is { } fc)
            return await PageByForecast(filtered, cardParams, folder, fc.metric, fc.horizon, fc.desc);

        // When a specific grade tier is shown AND the sort or range filter keys on
        // its price — which lives in a different DbContext (priceCharting) — it
        // can't be handled in SQL alongside the card query. Sort/filter/paginate
        // in memory instead, so the displayed price and the rows agree.
        if (!string.IsNullOrEmpty(cardParams.Grade)
            && (IsPriceSort(cardParams.OrderBy) || HasPriceRange(cardParams)))
            return await PageByGradePrice(filtered, cardParams, folder);

        var query = filtered.Sort(cardParams.OrderBy);
        var paged = await PagedList<CardBase>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = paged.Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id))).ToList();
        await ApplyGradePrice(cards, folder, cardParams.Grade);
        await ApplyMarket(cards, folder, cardParams.Grade, cardParams.Trend);
        return cards;
    }

    private static bool IsPriceSort(string? orderBy) => orderBy is "price" or "priceDesc";

    // Historical growth sorts: hist{Pct|Usd}{1w|1m|6m|1y}[Desc].
    private static (string metric, string window, bool desc)? ParseHistorySort(string? o) => o switch
    {
        "histPct1w" => ("pct", "1w", false),  "histPct1wDesc" => ("pct", "1w", true),
        "histPct1m" => ("pct", "1m", false),  "histPct1mDesc" => ("pct", "1m", true),
        "histPct6m" => ("pct", "6m", false),  "histPct6mDesc" => ("pct", "6m", true),
        "histPct1y" => ("pct", "1y", false),  "histPct1yDesc" => ("pct", "1y", true),
        "histUsd1w" => ("usd", "1w", false),  "histUsd1wDesc" => ("usd", "1w", true),
        "histUsd1m" => ("usd", "1m", false),  "histUsd1mDesc" => ("usd", "1m", true),
        "histUsd6m" => ("usd", "6m", false),  "histUsd6mDesc" => ("usd", "6m", true),
        "histUsd1y" => ("usd", "1y", false),  "histUsd1yDesc" => ("usd", "1y", true),
        _ => null,
    };

    // Forecast sorts: chg{Pct|Usd}{1w|1m|6|12}[Desc] -> (metric, horizon, descending).
    private static (string metric, string horizon, bool desc)? ParseForecastSort(string? o) => o switch
    {
        "chgPct1w" => ("pct", "1w", false),  "chgPct1wDesc" => ("pct", "1w", true),
        "chgPct1m" => ("pct", "1m", false),  "chgPct1mDesc" => ("pct", "1m", true),
        "chgPct6" => ("pct", "6m", false),   "chgPct6Desc" => ("pct", "6m", true),
        "chgPct12" => ("pct", "12m", false), "chgPct12Desc" => ("pct", "12m", true),
        "chgUsd1w" => ("usd", "1w", false),  "chgUsd1wDesc" => ("usd", "1w", true),
        "chgUsd1m" => ("usd", "1m", false),  "chgUsd1mDesc" => ("usd", "1m", true),
        "chgUsd6" => ("usd", "6m", false),   "chgUsd6Desc" => ("usd", "6m", true),
        "chgUsd12" => ("usd", "12m", false), "chgUsd12Desc" => ("usd", "12m", true),
        _ => null,
    };


    // Penny cards turn rounding noise into "+500% growth" and bury every real
    // card at the top of the forecast sorts — below this base price a card's
    // forecast can't rank it (it still lists, in the unsorted tail).
    private const double MinForecastSortBase = 5.0;

    // Expected change per product for one (target, horizon), from predictions.db:
    // the % and USD delta plus the from (current) and to (forecast) prices.
    private async Task<Dictionary<int, (double pct, double usd, double from, double to)>> ForecastChanges(
        string game, string target, string horizon, List<int> ids)
    {
        if (ids.Count == 0) return [];
        var rows = await predictions.Forecasts
            .Where(f => f.Game == game && f.Target == target && f.Horizon == horizon
                        && f.BasePrice >= MinForecastSortBase && ids.Contains(f.ProductId))
            .Select(f => new { f.ProductId, f.BasePrice, f.ForecastPrice })
            .ToListAsync();
        return rows.ToDictionary(r => r.ProductId, r => (
            pct: r.BasePrice > 0 ? (r.ForecastPrice / r.BasePrice - 1) * 100 : 0.0,
            usd: r.ForecastPrice - r.BasePrice,
            from: r.BasePrice,
            to: r.ForecastPrice));
    }

    private static void SetExpected(
        CardDto card, (double pct, double usd, double from, double to) ch, string metric, string horizon)
    {
        card.ExpectedChange = metric == "pct" ? ch.pct : ch.usd;
        card.ExpectedUnit = metric == "pct" ? "percent" : "usd";
        card.ExpectedHorizon = horizon;
        card.ExpectedFrom = ch.from;
        card.ExpectedTo = ch.to;
    }

    // Sort + paginate the filtered set by an expected forecast change (cross-DB, so
    // in memory). Cards without a forecast sort to the end and keep showing their price.
    private async Task<List<CardDto>> PageByForecast(
        IQueryable<CardBase> filtered, CardParams p, string folder,
        string metric, string horizon, bool desc)
    {
        var all = await filtered.ToListAsync();

        // Range on a selected tier's shown price (the ungraded case was already
        // filtered in SQL before this call).
        if (!string.IsNullOrEmpty(p.Grade) && HasPriceRange(p))
        {
            var shown = await GradePrices(folder, p.Grade, all.Select(c => c.Id).ToList());
            all = all.Where(c => shown.TryGetValue(c.Id, out var v) && InPriceRange(v, p)).ToList();
        }

        var changes = await ForecastChanges(folder, GradeTiers.ForecastTarget(p.Grade), horizon, all.Select(c => c.Id).ToList());
        double Key((double pct, double usd, double from, double to) ch) => metric == "pct" ? ch.pct : ch.usd;

        var withFc = all.Where(c => changes.ContainsKey(c.Id));
        var without = all.Where(c => !changes.ContainsKey(c.Id));
        var sorted = (desc ? withFc.OrderByDescending(c => Key(changes[c.Id]))
                           : withFc.OrderBy(c => Key(changes[c.Id])))
            .Concat(without)
            .ToList();

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = sorted.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(sorted.Count / (double)p.PageSize),
        });

        var cards = sorted
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id)))
            .ToList();

        await ApplyGradePrice(cards, folder, p.Grade);
        foreach (var card in cards)
            if (changes.TryGetValue(card.Id, out var ch)) SetExpected(card, ch, metric, horizon);
        await ApplyMarket(cards, folder, p.Grade, p.Trend);
        return cards;
    }

    // Sort + paginate by ACTUAL price growth over one trend window, computed on
    // the shown tier's history — the same anchor rule the tiles' PAST pill uses
    // (last point at-or-before the window start, else the first point), so the
    // row order always agrees with the displayed movement.
    private async Task<List<CardDto>> PageByHistory(
        IQueryable<CardBase> filtered, CardParams p, string folder,
        string metric, string window, bool desc)
    {
        var all = await filtered.ToListAsync();

        // Range on a selected tier's shown price (ungraded was filtered in SQL).
        if (!string.IsNullOrEmpty(p.Grade) && HasPriceRange(p))
        {
            var shown = await GradePrices(folder, p.Grade, all.Select(c => c.Id).ToList());
            all = all.Where(c => shown.TryGetValue(c.Id, out var v) && InPriceRange(v, p)).ToList();
        }

        var tier = GradeTiers.PriceTier(p.Grade ?? "");
        var changes = await HistoryChanges(folder, tier, all.Select(c => c.Id).ToList(), window);

        // % ranking excludes floor-suppressed penny cards (Pct == null); a $
        // move can't be faked by rounding, so every card with history ranks.
        double? Key(CardBase c) => changes.TryGetValue(c.Id, out var ch)
            ? (metric == "pct" ? ch.Pct : ch.Usd)
            : null;
        var withChg = all.Where(c => Key(c) != null);
        var without = all.Where(c => Key(c) == null);   // no history / floored -> end
        var sorted = (desc ? withChg.OrderByDescending(c => Key(c))
                           : withChg.OrderBy(c => Key(c)))
            .Concat(without)
            .ToList();

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = sorted.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(sorted.Count / (double)p.PageSize),
        });

        var cards = sorted
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id)))
            .ToList();

        await ApplyGradePrice(cards, folder, p.Grade);
        await ApplyMarket(cards, folder, p.Grade, window);   // tiles trend over the sorted window
        return cards;
    }

    // Price change per product over one trend window, from the tier's history:
    // % (null when floored) and absolute $.
    private async Task<Dictionary<int, (double? Pct, double Usd)>> HistoryChanges(
        string game, string tier, List<int> ids, string window)
    {
        if (ids.Count == 0) return [];
        var cutoff = TrendWindows[window].Start().ToString("yyyy-MM-dd");

        var rows = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();

        var changes = new Dictionary<int, (double? Pct, double Usd)>();
        foreach (var g in rows.GroupBy(r => r.ProductId))
        {
            var series = g.OrderBy(r => r.Date).ToList();
            var latest = series[^1];
            var anchor = series.LastOrDefault(r => string.CompareOrdinal(r.Date, cutoff) <= 0)
                         ?? series[0];
            if (anchor.Price <= 0) continue;
            // Same floor as the forecast sorts, for % only: penny cards turn
            // rounding noise into +10,000% and bury every real mover.
            double? pct = anchor.Price >= MinForecastSortBase
                ? (latest.Price / anchor.Price - 1) * 100
                : null;
            changes[g.Key] = (pct, latest.Price - anchor.Price);
        }
        return changes;
    }

    // Sort + paginate the full filtered set by a selected grade tier's price. That price
    // comes from priceCharting.History (a separate context), so we pull the filtered cards
    // and the tier's latest prices into memory and join them here. Cards with no price for
    // the tier sort to the end (in both directions), matching their '—' display.
    private async Task<List<CardDto>> PageByGradePrice(
        IQueryable<CardBase> filtered, CardParams p, string folder)
    {
        var all = await filtered.ToListAsync();
        var prices = await GradePrices(folder, p.Grade!, all.Select(c => c.Id).ToList());

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

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = sorted.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(sorted.Count / (double)p.PageSize),
        });

        var cards = sorted
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id)))
            .ToList();

        foreach (var card in cards)
            card.Price = prices.TryGetValue(card.Id, out var v) ? v : null;
        await ApplyMarket(cards, folder, p.Grade, p.Trend);
        return cards;
    }

    // Like Page, but restricted to a specific set of product ids (the user's tracked
    // list). The default sort follows the tracked order (AddedAt lives in store.db, so
    // it's applied in memory); explicit sorts (name/price) work as in the catalog.
    private async Task<List<CardDto>> PageScoped(
        IQueryable<CardBase> source, CardParams p, string folder, List<int> orderedIds)
    {
        var matched = orderedIds.Count == 0
            ? []
            : await source
                .Where(c => orderedIds.Contains(c.Id))
                .Search(p.SearchTerm)
                .Filter(p.Sets, p.Rarities)
                .ToListAsync();

        var rank = new Dictionary<int, int>();
        for (var i = 0; i < orderedIds.Count; i++) rank[orderedIds[i]] = i;

        // Price sort must key off the SAME value we display: the selected grade tier's
        // price when one is shown, otherwise the Near Mint column.
        var gradePrices = string.IsNullOrEmpty(p.Grade)
            ? null
            : await GradePrices(folder, p.Grade, matched.Select(c => c.Id).ToList());
        double? PriceOf(CardBase c) => gradePrices != null
            ? (gradePrices.TryGetValue(c.Id, out var v) ? v : null)
            : c.NearMintPrice;

        // Sorting is by ACTUAL past growth (history), MODEL forecast growth, or a
        // plain field — same vocabulary as the catalog, restricted to the tracked ids.
        var hs = ParseHistorySort(p.OrderBy);
        var fc = hs is null ? ParseForecastSort(p.OrderBy) : null;
        var ids = matched.Select(c => c.Id).ToList();
        var histChanges = hs is { } h ? await HistoryChanges(folder, GradeTiers.PriceTier(p.Grade ?? ""), ids, h.window) : null;
        var changes = fc is { } fcv ? await ForecastChanges(folder, GradeTiers.ForecastTarget(p.Grade), fcv.horizon, ids) : null;

        List<CardBase> ordered;
        if (hs is { } hsv && histChanges != null)
        {
            double? Key(CardBase c) => histChanges.TryGetValue(c.Id, out var ch)
                ? (hsv.metric == "pct" ? ch.Pct : ch.Usd) : null;
            var withChg = matched.Where(c => Key(c) != null);
            var without = matched.Where(c => Key(c) == null);   // no history / floored -> end
            ordered = (hsv.desc ? withChg.OrderByDescending(Key) : withChg.OrderBy(Key))
                .Concat(without).ToList();
        }
        else if (fc is { } f && changes != null)
        {
            double Key(CardBase c) => changes.TryGetValue(c.Id, out var ch)
                ? (f.metric == "pct" ? ch.pct : ch.usd)
                : (f.desc ? double.NegativeInfinity : double.PositiveInfinity);  // no forecast -> end
            ordered = (f.desc ? matched.OrderByDescending(Key) : matched.OrderBy(Key)).ToList();
        }
        else
        {
            ordered = (p.OrderBy switch
            {
                "price" => matched.OrderBy(PriceOf),
                "priceDesc" => matched.OrderByDescending(PriceOf),
                "name" => matched.OrderBy(c => c.Name),
                _ => matched.OrderBy(c => rank.GetValueOrDefault(c.Id, int.MaxValue)),  // order added
            }).ToList();
        }

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = ordered.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(ordered.Count / (double)p.PageSize),
        });

        var cards = ordered
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(c => c.ToDto(folder, CardImageUrl(folder, c.Id)))
            .ToList();

        await ApplyGradePrice(cards, folder, p.Grade);
        if (fc is { } fs && changes != null)
            foreach (var card in cards)
                if (changes.TryGetValue(card.Id, out var ch)) SetExpected(card, ch, fs.metric, fs.horizon);
        // A history sort trends the tiles over the sorted window (as the catalog does);
        // otherwise use the requested trend window.
        await ApplyMarket(cards, folder, p.Grade, hs?.window ?? p.Trend);
        return cards;
    }

    // The Owned list, expanded to one tile per (card + condition). Each tile carries
    // its quantity and the individual copies at that condition, is priced by that
    // condition's market price, and honors the same search/filter/sort/paging.
    private async Task<List<CardDto>> PageOwnedByCondition(
        IQueryable<CardBase> source, CardParams p, string folder)
    {
        var user = User.Identity!.Name!;
        var game = folder;

        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.Kind == TrackKind.Owned)
            .ToListAsync();

        // Card rows for the owned products, honoring search / set / rarity filters.
        var ids = copies.Select(x => x.ProductId).Distinct().ToList();
        var cardById = new Dictionary<int, CardBase>();
        if (ids.Count > 0)
        {
            var matched = await source.Where(c => ids.Contains(c.Id))
                .Search(p.SearchTerm).Filter(p.Sets, p.Rarities).ToListAsync();
            cardById = matched.ToDictionary(c => c.Id);
        }

        // Group copies into display units, dropping cards filtered out above.
        // Blank copies (no purchase detail) stack into one unit per (card, condition)
        // with a quantity; every copy with detail (paid/date/note) is its own unit.
        var units = copies
            .Where(x => cardById.ContainsKey(x.ProductId))
            .GroupBy(x => new { x.ProductId, Grade = x.Grade ?? "" })
            .SelectMany(g =>
            {
                var detailedUnits = g.Where(x => x.HasDetail).Select(x => new
                {
                    g.Key.ProductId,
                    g.Key.Grade,
                    Copies = new List<TrackedCard> { x },
                    LastAdded = x.AddedAt,
                });
                var blanks = g.Where(x => !x.HasDetail).OrderBy(x => x.AddedAt).ToList();
                return blanks.Count == 0
                    ? detailedUnits
                    : detailedUnits.Append(new
                    {
                        g.Key.ProductId,
                        g.Key.Grade,
                        Copies = blanks,
                        LastAdded = blanks.Max(x => x.AddedAt),
                    });
            })
            .ToList();

        // Latest price for each unit, keyed by its condition's price tier.
        var priceByTier = new Dictionary<string, Dictionary<int, double>>();
        foreach (var tier in units.Select(u => GradeTiers.PriceTier(u.Grade)).Distinct())
        {
            var tierIds = units.Where(u => GradeTiers.PriceTier(u.Grade) == tier).Select(u => u.ProductId).Distinct().ToList();
            priceByTier[tier] = await GradePrices(folder, tier, tierIds);
        }
        double? UnitPrice(int pid, string grade) =>
            priceByTier[GradeTiers.PriceTier(grade)].TryGetValue(pid, out var v) ? v : null;

        // Expected forecast change per unit, priced against the tile's own condition tier.
        var fc = ParseForecastSort(p.OrderBy);
        var changesByTarget = new Dictionary<string, Dictionary<int, (double pct, double usd, double from, double to)>>();
        if (fc is not null)
            foreach (var tgt in units.Select(u => GradeTiers.ForecastTarget(u.Grade)).Distinct())
            {
                var tIds = units.Where(u => GradeTiers.ForecastTarget(u.Grade) == tgt).Select(u => u.ProductId).Distinct().ToList();
                changesByTarget[tgt] = await ForecastChanges(folder, tgt, fc.Value.horizon, tIds);
            }
        (double pct, double usd, double from, double to)? UnitChange(int pid, string grade) =>
            changesByTarget.TryGetValue(GradeTiers.ForecastTarget(grade), out var d) && d.TryGetValue(pid, out var ch) ? ch : null;
        double ChangeKey(int pid, string grade, (string metric, string horizon, bool desc) f) =>
            UnitChange(pid, grade) is { } ch
                ? (f.metric == "pct" ? ch.pct : ch.usd)
                : (f.desc ? double.NegativeInfinity : double.PositiveInfinity);  // no forecast -> end

        // Actual price-history change per unit (the Trend column header sort),
        // computed against each unit's own condition tier.
        var hs = ParseHistorySort(p.OrderBy);
        var histByTier = new Dictionary<string, Dictionary<int, (double? Pct, double Usd)>>();
        if (hs is not null)
            foreach (var tier in units.Select(u => GradeTiers.PriceTier(u.Grade)).Distinct())
            {
                var tIds = units.Where(u => GradeTiers.PriceTier(u.Grade) == tier)
                    .Select(u => u.ProductId).Distinct().ToList();
                histByTier[tier] = await HistoryChanges(folder, tier, tIds, hs.Value.window);
            }
        double HistKey(int pid, string grade, (string metric, string window, bool desc) h)
        {
            var missing = h.desc ? double.NegativeInfinity : double.PositiveInfinity;
            return histByTier.TryGetValue(GradeTiers.PriceTier(grade), out var d) && d.TryGetValue(pid, out var ch)
                ? (h.metric == "pct" ? ch.Pct ?? missing : ch.Usd)
                : missing;
        }

        // Unit-level sort keys for the positions table's clickable headers.
        // Nulls (no paid data / no price) always sink to the end.
        double? PaidOf(List<TrackedCard> cs) =>
            cs.Any(x => x.PurchasePrice is > 0) ? cs.Sum(x => x.PurchasePrice ?? 0) : null;
        double? ValueOf(int pid, string grade, int qty) =>
            UnitPrice(pid, grade) is { } v ? v * qty : null;
        double? PlOf(int pid, string grade, List<TrackedCard> cs) =>
            PaidOf(cs) is { } paid && ValueOf(pid, grade, cs.Count) is { } val ? val - paid : null;
        int CondRank(string grade) =>
            Array.IndexOf(ConditionOrder, grade) is var i && i >= 0 ? i : int.MaxValue;

        var ordered = (fc is { } fk
            ? (fk.desc
                ? units.OrderByDescending(u => ChangeKey(u.ProductId, u.Grade, fk))
                : units.OrderBy(u => ChangeKey(u.ProductId, u.Grade, fk)))
            : hs is { } hk
            ? (hk.desc
                ? units.OrderByDescending(u => HistKey(u.ProductId, u.Grade, hk))
                : units.OrderBy(u => HistKey(u.ProductId, u.Grade, hk)))
            : p.OrderBy switch
            {
                "price" => units.OrderBy(u => UnitPrice(u.ProductId, u.Grade)),
                "priceDesc" => units.OrderByDescending(u => UnitPrice(u.ProductId, u.Grade)),
                "value" => units.OrderBy(u => ValueOf(u.ProductId, u.Grade, u.Copies.Count) ?? double.PositiveInfinity),
                "valueDesc" => units.OrderByDescending(u => ValueOf(u.ProductId, u.Grade, u.Copies.Count) ?? double.NegativeInfinity),
                "paid" => units.OrderBy(u => PaidOf(u.Copies) ?? double.PositiveInfinity),
                "paidDesc" => units.OrderByDescending(u => PaidOf(u.Copies) ?? double.NegativeInfinity),
                "pl" => units.OrderBy(u => PlOf(u.ProductId, u.Grade, u.Copies) ?? double.PositiveInfinity),
                "plDesc" => units.OrderByDescending(u => PlOf(u.ProductId, u.Grade, u.Copies) ?? double.NegativeInfinity),
                "qty" => units.OrderBy(u => u.Copies.Count),
                "qtyDesc" => units.OrderByDescending(u => u.Copies.Count),
                "condition" => units.OrderBy(u => CondRank(u.Grade)),
                "conditionDesc" => units.OrderByDescending(u => CondRank(u.Grade)),
                "name" => units.OrderBy(u => cardById[u.ProductId].Name),
                "nameDesc" => units.OrderByDescending(u => cardById[u.ProductId].Name),
                _ => units.OrderByDescending(u => u.LastAdded),   // order added
            }).ToList();

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = ordered.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(ordered.Count / (double)p.PageSize),
        });

        var dtos = ordered
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(u =>
            {
                var card = cardById[u.ProductId];
                var dto = card.ToDto(folder, CardImageUrl(folder, card.Id));
                dto.Price = UnitPrice(u.ProductId, u.Grade);
                dto.OwnedGrade = u.Grade.Length == 0 ? null : u.Grade;
                dto.OwnedQuantity = u.Copies.Count;
                dto.OwnedCopies = u.Copies.Select(x => new OwnedCopyDto
                {
                    Id = x.Id,
                    Grade = x.Grade,
                    // Owned copies are never priceless/dateless: 0 and AddedAt
                    // stand in for legacy rows the migration backfill missed.
                    PurchasePrice = x.PurchasePrice ?? 0,
                    AcquiredAt = x.AcquiredAt ?? x.AddedAt,
                    AutoPrice = x.AutoPrice,
                    Note = x.Note,
                    AddedAt = x.AddedAt,
                }).ToList();
                if (fc is { } fp && UnitChange(u.ProductId, u.Grade) is { } chp)
                    SetExpected(dto, chp, fp.metric, fp.horizon);
                return dto;
            })
            .ToList();

        // Each owned unit trends against its OWN condition tier; group by the
        // effective tier so e.g. "nm" and unspecified share one query pair.
        foreach (var group in dtos.GroupBy(d =>
                     (GradeTiers.PriceTier(d.OwnedGrade), GradeTiers.ForecastTarget(d.OwnedGrade))))
            await ApplyMarket(group.ToList(), folder, group.First().OwnedGrade ?? "", hs?.window ?? p.Trend);
        return dtos;
    }


    // When a grade/condition tier is explicitly selected, override the headline with
    // that tier's latest price from price_history_unified (same source as the forecast
    // "Current"). The default Near Mint price is the near_mint_price column (set in ToDto).
    private async Task ApplyGradePrice(List<CardDto> cards, string game, string? grade)
    {
        if (string.IsNullOrEmpty(grade) || cards.Count == 0) return;

        var latest = await GradePrices(game, grade, cards.Select(c => c.Id).ToList());
        foreach (var card in cards)
            card.Price = latest.TryGetValue(card.Id, out var p) ? p : null;
    }

    // One row per trend window: when the window starts, and which trained
    // forecast horizon the tile's headline forecast should use. History points
    // are monthly, so short windows read as "the last known price N ago".
    private static readonly Dictionary<string, (Func<DateTime> Start, string FcstHorizon)> TrendWindows = new()
    {
        ["1w"] = (() => DateTime.UtcNow.AddDays(-7), "1w"),
        ["1m"] = (() => DateTime.UtcNow.AddMonths(-1), "1m"),
        ["6m"] = (() => DateTime.UtcNow.AddMonths(-6), "6m"),
        ["1y"] = (() => DateTime.UtcNow.AddYears(-1), "12m"),
    };

    private static string NormalizeTrend(string? trend) =>
        trend != null && TrendWindows.ContainsKey(trend.ToLowerInvariant()) ? trend.ToLowerInvariant() : "1m";

    // Lightweight per-card market context for tiles / screener rows: a sparkline
    // and price movement over ONE shared trend window (so the graph and the
    // "$from → $to" figures always agree), plus the 6m/12m forecast changes.
    // Everything is computed for the SHOWN condition tier (Near Mint when none is
    // selected; conditions without their own forecast, like LP/MP, fall back to
    // the ungraded forecast). One history + one forecast query per page.
    private async Task ApplyMarket(List<CardDto> cards, string game, string? grade = null, string? trend = null, string? fcstOverride = null)
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
        var cutoff = window.Start().ToString("yyyy-MM-dd");

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

    // Products with a current price at the tier, from the snapshot table (one
    // row per card, so this is a single cheap scan).
    private async Task<HashSet<int>> TierPricedIds(string game, string tier)
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

    // Latest (most recent date) price per product for one grade tier, from history.
    private async Task<Dictionary<int, double>> GradePrices(string game, string grade, List<int> ids)
    {
        if (ids.Count == 0) return [];

        var rows = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == grade && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();

        return rows
            .GroupBy(r => r.ProductId)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(r => r.Date).First().Price);
    }

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
