using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;

namespace API.Controllers;

// Catalog endpoints. The tracked-list endpoints (owned/wishlist) live in
// CardsController.Tracked.cs; cross-database market lookups in CardMarketData;
// the movers ranking in MoverService.
public partial class CardsController(
    CardSources sources,
    PredictionsContext predictions, PriceChartingContext priceCharting,
    StoreContext store, ReasoningService reasoning,
    CardMarketData market, MoverService movers, IMemoryCache cache) : BaseApiController
{
    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        var game = GameRegistry.KeyOrDefault(cardParams.Game);
        return await Page(sources.Cards(game).VisibleInCatalog(), cardParams, game);
    }

    [HttpGet("{game}/{id:int}")]
    public async Task<ActionResult<CardDto>> GetCard(string game, int id,
        [FromQuery] string? printing = null)
    {
        var folder = GameRegistry.KeyOrDefault(game);

        // Art-pending cards are stored but not served (a direct link 404s too).
        var card = await sources.Find(folder, id);
        var dto = string.IsNullOrEmpty(card?.ImagePath)
            ? null
            : card!.ToDto(folder, CardImageUrl(folder, card.Id));

        if (dto == null) return NotFound();

        var nonBasePrinting = false;
        // A selected non-base printing swaps the headline price and the graded
        // ladder onto that printing's own (labeled) series; the base printing
        // keeps the regular columns/snapshot path.
        if (!string.IsNullOrEmpty(printing) && printing != dto.BasePrinting
            && dto.Printings?.Contains(printing) == true)
        {
            dto.GradedPrices = await PrintingLadder(folder, id, printing);
            dto.Price = dto.GradedPrices?.Ungraded ?? dto.Price;
            nonBasePrinting = true;
        }
        else
        {
            dto.GradedPrices = await GetGradedPrices(folder, id);
        }

        await market.ApplyMarket([dto], folder);   // PriceAsOf + market context for the header
        if (nonBasePrinting)
        {
            // Market decorations above are BASE-series-derived; scrub the
            // forecast-flavored ones so no base forecast leaks onto another
            // printing's page. (Sparkline/trend visuals come from the chart's
            // own printing-aware query.)
            dto.FcstTo = null;
            dto.Fcst12To = null;
            dto.ExpectedChange = null;
            dto.ExpectedFrom = null;
            dto.ExpectedTo = null;
        }
        return dto;   // headline price is the near_mint_price column, set in ToDto
    }

    // True when a selected printing is NOT the card's base printing — the
    // series forecasts/decorations are trained on base only.
    private async Task<bool> IsNonBasePrinting(string game, int id, string? printing)
    {
        if (string.IsNullOrEmpty(printing)) return false;
        var card = await sources.Find(game, id);
        return card?.BasePrinting != null && printing != card.BasePrinting;
    }

    // Graded ladder for a NON-BASE printing: latest labeled unified point per
    // tier (there is no snapshot table for printings — the series ARE the data).
    private async Task<GradedPriceDto?> PrintingLadder(string game, int id, string printing)
    {
        var points = await priceCharting.History.IgnoreQueryFilters()
            .Where(h => h.Game == game && h.ProductId == id && h.Printing == printing)
            .ToListAsync();
        if (points.Count == 0) return null;
        var latest = points
            .GroupBy(p => p.Grade)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(p => p.Date).First());
        double? Tier(string grade) =>
            latest.TryGetValue(grade, out var p) ? p.Price : null;
        return new GradedPriceDto
        {
            Ungraded = Tier("ungraded"),
            Grade7 = Tier("grade7"),
            Grade8 = Tier("grade8"),
            Grade9 = Tier("grade9"),
            Grade95 = Tier("grade95"),
            Psa10 = Tier("psa10"),
            UpdatedAt = latest.Values.Max(p => p.Date),
        };
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

        // The game's actual printing vocabulary (2026-08-28): the client only
        // shows the Printing filter when a game genuinely has variants, and
        // with the game's own names instead of a global hardcoded list. The
        // distinct JSON combos are few, so parse+union is cheap.
        var printingCombos = await sources.Cards(key).VisibleInCatalog()
            .Select(c => c.Printings)
            .Where(p => p != null && p != "")
            .Distinct()
            .ToListAsync();
        var printings = printingCombos
            .SelectMany(p =>
            {
                try { return System.Text.Json.JsonSerializer.Deserialize<string[]>(p!) ?? []; }
                catch (System.Text.Json.JsonException) { return []; }
            })
            .Distinct()
            .OrderBy(p => p)
            .ToList();

        return Ok(new { sets, rarities, hasYear, printings });
    }

    // Monthly price history per condition tier, for charting (TradingView-style).
    [HttpGet("{game}/{id:int}/history")]
    public async Task<IActionResult> GetHistory(string game, int id, [FromQuery] string? grade,
        [FromQuery] string? printing = null)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var query = priceCharting.History.Where(h => h.Game == key && h.ProductId == id);
        if (!string.IsNullOrEmpty(printing))
            query = priceCharting.History.IgnoreQueryFilters()
                .Where(h => h.Game == key && h.ProductId == id && h.Printing == printing);
        if (!string.IsNullOrEmpty(grade)) query = query.Where(h => h.Grade == grade);

        var points = await query.OrderBy(h => h.Date).ToListAsync();
        var series = points
            .GroupBy(p => p.Grade)
            .ToDictionary(g => g.Key, g => g.Select(p => new { p.Date, p.Price, p.Source }).ToList());

        // Detail merge (2026-08-15): the unified table compresses ungraded to
        // month buckets, but the nightly crawl stores dated NM prices (daily
        // fleet-wide since 2026-07-28; weekly further back for backfilled
        // cards). Serve them between the unified points — unified wins date
        // collisions, so price_corrections stay authoritative.
        if (string.IsNullOrEmpty(grade) || grade == "ungraded")
        {
            var nmQuery = string.IsNullOrEmpty(printing)
                ? priceCharting.NmDaily.Where(p => p.Game == key && p.ProductId == id)
                : priceCharting.NmDaily.IgnoreQueryFilters()
                    .Where(p => p.Game == key && p.ProductId == id && p.Printing == printing);
            var nm = await nmQuery.Where(p => p.Price > 0).ToListAsync();
            if (nm.Count > 0)
            {
                var ug = series.TryGetValue("ungraded", out var existing)
                    ? existing
                    : [];
                var have = ug.Select(p => p.Date).ToHashSet();

                // Cutover bridge (2026-08-15): unify drops PC ungraded from
                // July 2026 in favor of TCGplayer, but a card whose NM crawl
                // started late in the cutover has a hole PC actually priced
                // (often daily, from the subscription's final weeks) — e.g.
                // an 8-week straight-line gap ending at the first NM point.
                // Fill [switch, first NM date) from the raw PC rows; unified
                // and NM points win any date collision.
                var firstNm = nm.Min(p => p.Date);
                var bridge = new List<PcRawPoint>();
                if (string.Compare(firstNm, "2026-07-01") > 0)
                {
                    var brQuery = string.IsNullOrEmpty(printing)
                        ? priceCharting.PcRaw.Where(p => p.Game == key && p.ProductId == id)
                        : priceCharting.PcRaw.IgnoreQueryFilters()
                            .Where(p => p.Game == key && p.ProductId == id && p.Printing == printing);
                    bridge = await brQuery
                        .Where(p => p.Grade == "ungraded" && p.Price > 0
                                    && string.Compare(p.Date, "2026-07-01") >= 0
                                    && string.Compare(p.Date, firstNm) < 0)
                        .ToListAsync();
                }

                series["ungraded"] = ug
                    .Concat(nm.Where(p => !have.Contains(p.Date))
                              .Select(p => new { p.Date, p.Price, Source = (string?)"tcgplayer" }))
                    .Concat(bridge.Where(p => !have.Contains(p.Date))
                              .Select(p => new { p.Date, p.Price, Source = (string?)"pricecharting" }))
                    .OrderBy(p => p.Date)
                    .ToList();
            }
        }

        return Ok(new { game = key, productId = id, series });
    }

    // LLM-written plain-English "take" summarizing the forecast (cached; null when
    // no Anthropic key is configured or the card has no forecast). Rate-limited:
    // each cache miss is a paid Anthropic call.
    [HttpGet("{game}/{id:int}/reasoning")]
    [EnableRateLimiting("reasoning")]
    public async Task<IActionResult> GetReasoning(string game, int id)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var card = await sources.Find(key, id);
        var prose = await reasoning.GetAsync(key, id, card?.Name, card?.SetName);
        return Ok(new { game = key, productId = id, prose });
    }

    // Model price forecasts (1m/6m/12m per condition tier) with confidence bands.
    [HttpGet("{game}/{id:int}/forecast")]
    public async Task<IActionResult> GetForecast(string game, int id,
        [FromQuery] string? printing = null)
    {
        var key = GameRegistry.KeyOrDefault(game);
        // Per-printing forecasts (2026-08-10): a selected non-base printing
        // serves its OWN trained rows; none yet -> covered:false (the UI shows
        // its honest note until that printing's first nightly train).
        if (await IsNonBasePrinting(key, id, printing))
        {
            var prRows = await predictions.Forecasts.IgnoreQueryFilters()
                .Where(f => f.Game == key && f.ProductId == id && f.Printing == printing
                            && f.Horizon != "1w")
                .ToListAsync();
            if (prRows.Count == 0)
                return Ok(new { game = key, productId = id,
                                forecasts = Array.Empty<object>(), printingCovered = false });
            var prForecasts = prRows.Select(f => new
            {
                f.Target, f.Horizon,
                AsOf = f.AnchorDate ?? f.AsOf,
                f.BasePrice, f.ForecastPrice, f.Low, f.High, f.Ret, f.Reason, f.Confidence,
                Months = 0,
            });
            return Ok(new { game = key, productId = id, forecasts = prForecasts,
                            printingCovered = true });
        }
        // The site serves 1m/6m/12m only. 1w rows retired 2026-08-14; the
        // filters below remain as guards against strays. Historically: 1w was
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
    // said back then" on the chart. Display target dates use fixed week-based
    // lengths (1w=7d from issue time; 1m/6m/12m = 28/182/364d from the
    // anchoring price date) — the scorecard still grades on month buckets.
    // The archive starts 2026-07-09, so points accumulate from one
    // horizon-length after that.
    [HttpGet("{game}/{id:int}/forecast-history")]
    public async Task<IActionResult> GetForecastHistory(string game, int id,
        [FromQuery] string? printing = null)
    {
        var key = GameRegistry.KeyOrDefault(game);
        if (await IsNonBasePrinting(key, id, printing))
        {
            // Serve the selected printing's own archived cohorts (empty until
            // they mature — the chart just draws no overlays).
            var prPast = await predictions.ForecastArchive.IgnoreQueryFilters()
                .Where(f => f.Game == key && f.ProductId == id && f.Printing == printing
                            && f.ForecastPrice != null && f.Horizon != "1w")
                .ToListAsync();
            var prToday = DateTime.UtcNow.Date;
            var prRows = prPast
                .Select(f => new { f, TargetDate = ForecastTargetDate(f) })
                .Where(x => x.TargetDate != null && x.TargetDate <= prToday)
                .Select(x => new
                {
                    x.f.Target, x.f.Horizon,
                    TargetDate = x.TargetDate!.Value.ToString("yyyy-MM-dd"),
                    x.f.ForecastPrice, x.f.Low, x.f.High, x.f.BasePrice, x.f.AsOf,
                    IssuedAt = x.f.ScoredAt == null ? null : x.f.ScoredAt[..10],
                    x.f.RealizedPrice,
                });
            return Ok(new { game = key, productId = id, forecasts = prRows });
        }
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
                // TRUE generation date (scored_at) — AsOf is the anchor MONTH
                // BUCKET (always the 1st) and reads as a lie in "generated" UI.
                IssuedAt = x.f.ScoredAt == null ? null : x.f.ScoredAt[..10],
                x.f.ScoredAt,
                x.f.RealizedPrice,
            })
            .OrderBy(x => x.TargetDate)
            .ToList();

        return Ok(new { game = key, productId = id, forecasts = past });
    }

    // Fixed week-based horizon lengths (4/26/52 weeks). Calendar-month math
    // has no answer for "Aug 31 + 1 month"; days always do.
    private static readonly Dictionary<string, int> HorizonDays =
        new() { ["1m"] = 28, ["6m"] = 182, ["12m"] = 364 };

    private static DateTime? ForecastTargetDate(ArchivedForecast f)
    {
        // Graded rows pin the dot to the date the outcome was MEASURED
        // (realized_at): the computed due date can differ by a few days
        // (e.g. as_of+28d = Jul 29 vs the Aug 1 bucket that graded it), and
        // the vertical gap to the price line only reads as "the miss" when
        // both sit on the same date.
        if (f.RealizedAt != null && DateTime.TryParse(f.RealizedAt, out var realized))
            return realized.Date;
        // Pending rows: due = ISSUE date + horizon (a Jul 10 1m forecast is
        // due Aug 7). AsOf is the anchor month/cohort key, which for legacy
        // rows is the month's 1st — a fallback only.
        if (HorizonDays.TryGetValue(f.Horizon, out var days))
        {
            if (DateTime.TryParse(f.ScoredAt, out var issued))
                return issued.Date.AddDays(days);
            if (DateTime.TryParse(f.AsOf, out var asOf))
                return asOf.Date.AddDays(days);
        }
        return null;
    }

    // Search-by-photo: embed the uploaded image and return the closest cards
    // across every game. The image is processed entirely in memory and never
    // stored — it exists only for this request. 503 until the model artifacts
    // are shipped to this box.
    [HttpPost("image-search")]
    [EnableRateLimiting("imagesearch")]
    [RequestSizeLimit(10_000_000)]
    public async Task<IActionResult> ImageSearch(IFormFile image, [FromServices] ImageSearchHolder holder)
    {
        if (holder.Service == null)
            return StatusCode(503, "Image search isn't available on this server yet.");
        if (image == null || image.Length == 0) return BadRequest("No image uploaded.");

        List<ImageSearchService.Hit>? hits;
        await using (var stream = image.OpenReadStream())
        {
            hits = holder.Service.Search(stream);
        }
        if (hits == null) return BadRequest("That file doesn't look like an image.");

        var results = new List<object>();
        foreach (var h in hits)
        {
            var card = await sources.Find(h.Game, h.ProductId);
            if (card == null || string.IsNullOrEmpty(card.ImagePath)) continue;   // art-pending: not served
            results.Add(new
            {
                game = h.Game,
                productId = h.ProductId,
                name = card.Name,
                set = card.SetName,
                image = CardImageUrl(h.Game, h.ProductId),
                score = Math.Round(h.Score, 3),
            });
        }
        return Ok(results);
    }

    // Top movers across the games by ungraded forecast change — feeds the
    // market ticker and the home page tiles. The ranking is identical for every
    // visitor and the homepage alone requests it three times (hero, ticker,
    // tiles), so responses are cached for a few minutes per parameter set
    // instead of re-running the cross-database ranking each time.
    [HttpGet("movers")]
    public async Task<IActionResult> GetMovers(
        [FromQuery] int count = 12, [FromQuery] string? horizon = null, [FromQuery] string? trend = null,
        [FromQuery] int perGame = 0)
    {
        // Key on the NORMALIZED parameters (the same clamps/fallbacks the
        // ranking itself applies), so unrecognized values collapse onto the
        // entry they'd produce anyway instead of minting unbounded cache keys.
        count = Math.Clamp(count, 1, 24);
        perGame = Math.Clamp(perGame, 0, 6);
        horizon = horizon is "mix" or "1m" or "6m" ? horizon : "12m";
        trend = trend == null ? null : CardMarketData.NormalizeTrend(trend);   // null = per-game default

        var result = await cache.GetOrCreateAsync($"movers:{count}:{horizon}:{trend}:{perGame}", entry =>
        {
            // The ranking's inputs change once per day, at the nightly data
            // push — which restarts this process and empties the cache. A day
            // is therefore "until the data actually changes"; the old 5-minute
            // TTL made every quiet stretch of the day re-pay the ~25s cold
            // recompute (cold-homepage report 2026-08-27).
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromHours(24);
            return movers.TopMovers(count, horizon, trend, perGame, CardImageUrl);
        });
        return Ok(result);
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

        // Min/max on the SHOWN price. With no tier selected that's the Near Mint
        // column (filterable in SQL); a selected tier's price lives in another
        // DbContext, so those paths filter in memory below.
        if (string.IsNullOrEmpty(cardParams.Grade))
            filtered = filtered.PriceRange(cardParams.MinPrice, cardParams.MaxPrice);

        // Cross-DB id filters. Two filters key on data in other DbContexts:
        //   tier      — a selected tier lists only cards actually priced at it
        //               (no '—' rows);
        //   confidence — keep only cards whose SHOWN forecast (the priced
        //               tier's target at the trend window's horizon — the badge
        //               on the tile) carries a selected level; selecting every
        //               level still (deliberately) drops forecast-less cards.
        // Their id-sets reach tens of thousands on the big games, so they are
        // NEVER composed into SQL (an IN-list that size is SQLite's 'too many
        // SQL variables') — every paging path intersects in memory instead.
        HashSet<int>? idFilter = null;
        var tier = GradeTiers.PriceTier(cardParams.Grade ?? "");
        if (tier != "ungraded")
            idFilter = await market.TierPricedIds(folder, tier);
        var levels = (cardParams.Confidence ?? "")
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(l => l.ToLowerInvariant())
            .Where(l => l is "high" or "med" or "low").Distinct().ToArray();
        if (levels.Length > 0)
        {
            var confIds = await market.ConfidenceIds(
                folder, GradeTiers.ForecastTarget(cardParams.Grade),
                CardMarketData.ForecastHorizon(cardParams.Trend), levels);
            idFilter = idFilter == null ? confIds : idFilter.Intersect(confIds).ToHashSet();
        }

        // Printing filter: cards CARRYING one of the selected printings (the
        // printings column is a small JSON array — slim-scan + hash intersect,
        // same pattern as the other cross-DB id filters).
        var wantPrintings = (cardParams.Printings ?? "")
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToArray();
        if (wantPrintings.Length > 0)
        {
            var slimPr = await filtered.Select(c => new { c.Id, c.Printings }).ToListAsync();
            var prIds = slimPr
                .Where(r => r.Printings != null
                            && wantPrintings.Any(p => r.Printings.Contains($"\"{p}\"")))
                .Select(r => r.Id).ToHashSet();
            idFilter = idFilter == null ? prIds : idFilter.Intersect(prIds).ToHashSet();
        }

        if (CardSorts.History(cardParams.OrderBy) is { } historySort)
            return await PageByHistory(filtered, cardParams, folder, historySort, idFilter);

        if (CardSorts.Forecast(cardParams.OrderBy) is { } forecastSort)
            return await PageByForecast(filtered, cardParams, folder, forecastSort, idFilter);

        // When a specific grade tier is shown AND the sort or range filter keys on
        // its price — which lives in a different DbContext (priceCharting) — it
        // can't be handled in SQL alongside the card query. Sort/filter/paginate
        // in memory instead, so the displayed price and the rows agree.
        if (!string.IsNullOrEmpty(cardParams.Grade)
            && (CardSorts.IsPriceSort(cardParams.OrderBy) || HasPriceRange(cardParams)))
            return await PageByGradePrice(filtered, cardParams, folder, idFilter);

        // An active id filter forces in-memory paging here too (same SQLite
        // variable-limit reason) — but on a slim {id, sort keys} projection, not
        // full entities: magic is 100k+ rows and materializing them times out
        // the t3.small. Sort only ever keys on name or the NM price, so rank
        // ids from the projection, then fetch just the page's entities.
        if (idFilter != null)
        {
            var slim = (await filtered
                    .Select(c => new { c.Id, c.Name, c.NearMintPrice })
                    .ToListAsync())
                .Where(r => idFilter.Contains(r.Id)).ToList();
            var ranked = (cardParams.OrderBy switch
            {
                "price" => slim.OrderBy(r => r.NearMintPrice),
                "priceDesc" => slim.OrderByDescending(r => r.NearMintPrice),
                _ => slim.OrderBy(r => r.Name),
            }).ToList();
            var pageIds = PageSlice(ranked, cardParams).Select(r => r.Id).ToList();
            var page = await PageEntities(filtered, pageIds, folder);
            await market.ApplyGradePrice(page, folder, cardParams.Grade);
            await market.ApplyMarket(page, folder, cardParams.Grade, cardParams.Trend);
            return page;
        }

        var query = filtered.Sort(cardParams.OrderBy);
        var paged = await PagedList<CardBase>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = ToDtos(paged, folder);
        await market.ApplyGradePrice(cards, folder, cardParams.Grade);
        await market.ApplyMarket(cards, folder, cardParams.Grade, cardParams.Trend);
        return cards;
    }

    // Id set for the in-memory ranking paths: the filtered query's ids,
    // intersected with the cross-DB id filter and (when a tier's price range
    // is active) the shown-price range. Ids only — materializing a big game's
    // full entities is a 30s+ query on the t3.small; the ranked page fetches
    // its own entities afterwards (PageEntities).
    private async Task<List<int>> RankIds(
        IQueryable<CardBase> filtered, CardParams p, string folder, HashSet<int>? idFilter)
    {
        var ids = await filtered.Select(c => c.Id).ToListAsync();
        if (idFilter != null) ids = ids.Where(idFilter.Contains).ToList();
        if (!string.IsNullOrEmpty(p.Grade) && HasPriceRange(p))
        {
            var shown = await market.LatestTierPrices(folder, p.Grade, ids);
            ids = ids.Where(id => shown.TryGetValue(id, out var v) && InPriceRange(v, p)).ToList();
        }
        return ids;
    }

    // Fetch one ranked page's entities and emit DTOs in the ranked order.
    private async Task<List<CardDto>> PageEntities(
        IQueryable<CardBase> filtered, List<int> pageIds, string folder)
    {
        var entities = await filtered.Where(c => pageIds.Contains(c.Id)).ToListAsync();
        return ToDtos(pageIds.Select(id => entities.First(c => c.Id == id)), folder);
    }

    // Sort + paginate the filtered set by an expected forecast change (cross-DB, so
    // in memory). Cards without a forecast sort to the end and keep showing their price.
    private async Task<List<CardDto>> PageByForecast(
        IQueryable<CardBase> filtered, CardParams p, string folder, ForecastSort sort,
        HashSet<int>? idFilter = null)
    {
        var ids = await RankIds(filtered, p, folder, idFilter);
        var changes = await market.ForecastChanges(
            folder, GradeTiers.ForecastTarget(p.Grade), sort.Horizon, ids);
        double Key(int id) => sort.Metric == "pct" ? changes[id].Pct : changes[id].Usd;

        var withFc = ids.Where(changes.ContainsKey);
        var without = ids.Where(id => !changes.ContainsKey(id));
        var sorted = (sort.Descending
                ? withFc.OrderByDescending(Key)
                : withFc.OrderBy(Key))
            .Concat(without)
            .ToList();

        var cards = await PageEntities(filtered, PageSlice(sorted, p), folder);

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
        IQueryable<CardBase> filtered, CardParams p, string folder, HistorySort sort,
        HashSet<int>? idFilter = null)
    {
        var ids = await RankIds(filtered, p, folder, idFilter);
        var tier = GradeTiers.PriceTier(p.Grade ?? "");
        var changes = await market.HistoryChanges(folder, tier, ids, sort.Window);

        // Every card with history ranks by its true move; cards with no
        // history close the list.
        double Key(int id) => sort.Metric == "pct" ? changes[id].Pct : changes[id].Usd;
        var withChg = ids.Where(changes.ContainsKey);
        var noHistory = ids.Where(id => !changes.ContainsKey(id));
        var sorted = (sort.Descending
                ? withChg.OrderByDescending(Key)
                : withChg.OrderBy(Key))
            .Concat(noHistory)
            .ToList();

        var cards = await PageEntities(filtered, PageSlice(sorted, p), folder);

        await market.ApplyGradePrice(cards, folder, p.Grade);
        await market.ApplyMarket(cards, folder, p.Grade, sort.Window);   // tiles trend over the sorted window
        return cards;
    }

    // Sort + paginate the full filtered set by a selected grade tier's price (cross-DB,
    // so in memory). Cards with no price for the tier sort to the end (in both
    // directions), matching their '—' display.
    private async Task<List<CardDto>> PageByGradePrice(
        IQueryable<CardBase> filtered, CardParams p, string folder,
        HashSet<int>? idFilter = null)
    {
        var all = await filtered.ToListAsync();
        if (idFilter != null) all = all.Where(c => idFilter.Contains(c.Id)).ToList();
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
