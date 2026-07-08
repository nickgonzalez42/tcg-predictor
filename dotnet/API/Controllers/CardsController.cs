using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class CardsController(
    OnePieceContext onePiece, PokemonContext pokemon,
    PredictionsContext predictions, PriceChartingContext priceCharting,
    StoreContext store) : BaseApiController
{
    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        return IsPokemon(cardParams.Game)
            ? await Page(pokemon.Cards, cardParams, "pokemon", (c, url) => c.ToDto(url))
            : await Page(onePiece.Cards, cardParams, "onepiece", (c, url) => c.ToDto(url));
    }

    // One of the signed-in user's tracked lists (owned | wishlist), with the same
    // search/filter/sort/pagination as the catalog. Default sort = order added.
    [Authorize]
    [HttpGet("tracked")]
    public async Task<ActionResult<List<CardDto>>> GetTracked([FromQuery] CardParams cardParams, [FromQuery] string? kind)
    {
        var listKind = TrackKind.Normalize(kind);

        // Owned is shown one tile per (card + condition) with a quantity, so it has
        // its own paging path. Wishlist is one tile per card.
        if (listKind == TrackKind.Owned)
            return IsPokemon(cardParams.Game)
                ? await PageOwnedByCondition(pokemon.Cards, cardParams, "pokemon", (c, url) => c.ToDto(url))
                : await PageOwnedByCondition(onePiece.Cards, cardParams, "onepiece", (c, url) => c.ToDto(url));

        var user = User.Identity!.Name!;
        var game = GameKey(cardParams.Game);
        var trackedIds = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.Kind == listKind)
            .OrderByDescending(x => x.AddedAt)
            .Select(x => x.ProductId)
            .ToListAsync();

        return IsPokemon(cardParams.Game)
            ? await PageScoped(pokemon.Cards, cardParams, "pokemon", (c, url) => c.ToDto(url), trackedIds)
            : await PageScoped(onePiece.Cards, cardParams, "onepiece", (c, url) => c.ToDto(url), trackedIds);
    }

    [HttpGet("{game}/{id:int}")]
    public async Task<ActionResult<CardDto>> GetCard(string game, int id)
    {
        var folder = GameKey(game);

        CardDto? dto;
        if (IsPokemon(game))
        {
            var card = await pokemon.Cards.FindAsync(id);
            dto = card?.ToDto(ImageUrl(folder, card.Id));
        }
        else
        {
            var card = await onePiece.Cards.FindAsync(id);
            dto = card?.ToDto(ImageUrl(folder, card.Id));
        }

        if (dto == null) return NotFound();

        dto.GradedPrices = await GetGradedPrices(folder, id);
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
        };
    }

    [HttpGet("filters")]
    public async Task<IActionResult> GetFilters([FromQuery] string? game)
    {
        return IsPokemon(game)
            ? Ok(await Facets(pokemon.Cards))
            : Ok(await Facets(onePiece.Cards));
    }

    // Monthly price history per condition tier, for charting (TradingView-style).
    [HttpGet("{game}/{id:int}/history")]
    public async Task<IActionResult> GetHistory(string game, int id, [FromQuery] string? grade)
    {
        var key = GameKey(game);
        var query = priceCharting.History.Where(h => h.Game == key && h.ProductId == id);
        if (!string.IsNullOrEmpty(grade)) query = query.Where(h => h.Grade == grade);

        var points = await query.OrderBy(h => h.Date).ToListAsync();
        var series = points
            .GroupBy(p => p.Grade)
            .ToDictionary(g => g.Key, g => g.Select(p => new { p.Date, p.Price, p.Source }).ToList());

        return Ok(new { game = key, productId = id, series });
    }

    // Model price forecasts (6m/12m for ungraded + PSA 10) with confidence bands.
    [HttpGet("{game}/{id:int}/forecast")]
    public async Task<IActionResult> GetForecast(string game, int id)
    {
        var key = GameKey(game);
        var rows = await predictions.Forecasts
            .Where(f => f.Game == key && f.ProductId == id)
            .ToListAsync();

        // Months of history per tier — a proxy for how trustworthy the forecast is.
        var monthsByTier = await priceCharting.History
            .Where(h => h.Game == key && h.ProductId == id)
            .GroupBy(h => h.Grade)
            .Select(g => new { Grade = g.Key, Months = g.Count() })
            .ToDictionaryAsync(x => x.Grade, x => x.Months);

        var forecasts = rows.Select(f => new
        {
            f.Target, f.Horizon, f.AsOf, f.BasePrice,
            f.ForecastPrice, f.Low, f.High, f.Ret, f.Reason,
            Months = monthsByTier.GetValueOrDefault(f.Target, 0),
        });

        return Ok(new { game = key, productId = id, forecasts });
    }

    // Summary stats per tier (current, all-time high/low, % change windows).
    [HttpGet("{game}/{id:int}/stats")]
    public async Task<IActionResult> GetStats(string game, int id)
    {
        var key = GameKey(game);
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
            var target = latestDate.AddMonths(-months);
            var past = series.LastOrDefault(p => DateTime.Parse(p.Date) <= target);
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

    private async Task<List<CardDto>> Page<T>(
        IQueryable<T> source, CardParams cardParams, string folder, Func<T, string, CardDto> toDto)
        where T : CardBase
    {
        var filtered = source
            .Search(cardParams.SearchTerm)
            .Filter(cardParams.Sets, cardParams.Rarities);

        // Sorting by an expected forecast change (cross-DB: forecasts live in predictions.db).
        if (ParseForecastSort(cardParams.OrderBy) is { } fc)
            return await PageByForecast(filtered, cardParams, folder, toDto, fc.metric, fc.horizon, fc.desc);

        // When a specific grade tier is shown AND we're sorting by price, the sort key
        // is that tier's price — which lives in a different DbContext (priceCharting),
        // so it can't be ordered in SQL alongside the card query. Sort/paginate in memory
        // instead, so the displayed price and the row order agree.
        if (!string.IsNullOrEmpty(cardParams.Grade) && IsPriceSort(cardParams.OrderBy))
            return await PageByGradePrice(filtered, cardParams, folder, toDto);

        var query = filtered.Sort(cardParams.OrderBy);
        var paged = await PagedList<T>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = paged.Select(c => toDto(c, ImageUrl(folder, c.Id))).ToList();
        await ApplyGradePrice(cards, folder, cardParams.Grade);
        return cards;
    }

    private static bool IsPriceSort(string? orderBy) => orderBy is "price" or "priceDesc";

    // Forecast sorts: chg{Pct|Usd}{6|12}[Desc] -> (metric, horizon, descending).
    private static (string metric, string horizon, bool desc)? ParseForecastSort(string? o) => o switch
    {
        "chgPct6" => ("pct", "6m", false),   "chgPct6Desc" => ("pct", "6m", true),
        "chgPct12" => ("pct", "12m", false), "chgPct12Desc" => ("pct", "12m", true),
        "chgUsd6" => ("usd", "6m", false),   "chgUsd6Desc" => ("usd", "6m", true),
        "chgUsd12" => ("usd", "12m", false), "chgUsd12Desc" => ("usd", "12m", true),
        _ => null,
    };

    // Which forecast target a shown grade/condition maps to. Forecasts exist for
    // ungraded + the graded tiers; NM / played / unspecified all fall back to ungraded.
    private static readonly HashSet<string> GradedForecastTargets =
        ["grade7", "grade8", "grade9", "grade95", "psa10", "bgs10", "cgc10", "sgc10"];
    private static string ForecastTarget(string? grade) =>
        !string.IsNullOrEmpty(grade) && GradedForecastTargets.Contains(grade) ? grade : "ungraded";

    // Expected change per product for one (target, horizon), from predictions.db:
    // the % and USD delta plus the from (current) and to (forecast) prices.
    private async Task<Dictionary<int, (double pct, double usd, double from, double to)>> ForecastChanges(
        string game, string target, string horizon, List<int> ids)
    {
        if (ids.Count == 0) return [];
        var rows = await predictions.Forecasts
            .Where(f => f.Game == game && f.Target == target && f.Horizon == horizon && ids.Contains(f.ProductId))
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
    private async Task<List<CardDto>> PageByForecast<T>(
        IQueryable<T> filtered, CardParams p, string folder, Func<T, string, CardDto> toDto,
        string metric, string horizon, bool desc) where T : CardBase
    {
        var all = await filtered.ToListAsync();
        var changes = await ForecastChanges(folder, ForecastTarget(p.Grade), horizon, all.Select(c => c.Id).ToList());
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
            .Select(c => toDto(c, ImageUrl(folder, c.Id)))
            .ToList();

        await ApplyGradePrice(cards, folder, p.Grade);
        foreach (var card in cards)
            if (changes.TryGetValue(card.Id, out var ch)) SetExpected(card, ch, metric, horizon);
        return cards;
    }

    // Sort + paginate the full filtered set by a selected grade tier's price. That price
    // comes from priceCharting.History (a separate context), so we pull the filtered cards
    // and the tier's latest prices into memory and join them here. Cards with no price for
    // the tier sort to the end (in both directions), matching their '—' display.
    private async Task<List<CardDto>> PageByGradePrice<T>(
        IQueryable<T> filtered, CardParams p, string folder, Func<T, string, CardDto> toDto)
        where T : CardBase
    {
        var all = await filtered.ToListAsync();
        var prices = await GradePrices(folder, p.Grade!, all.Select(c => c.Id).ToList());

        var priced = all.Where(c => prices.ContainsKey(c.Id));
        var unpriced = all.Where(c => !prices.ContainsKey(c.Id));
        var sorted = (p.OrderBy == "priceDesc"
                ? priced.OrderByDescending(c => prices[c.Id])
                : priced.OrderBy(c => prices[c.Id]))
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
            .Select(c => toDto(c, ImageUrl(folder, c.Id)))
            .ToList();

        foreach (var card in cards)
            card.Price = prices.TryGetValue(card.Id, out var v) ? v : null;
        return cards;
    }

    // Like Page, but restricted to a specific set of product ids (the user's tracked
    // list). The default sort follows the tracked order (AddedAt lives in store.db, so
    // it's applied in memory); explicit sorts (name/price) work as in the catalog.
    private async Task<List<CardDto>> PageScoped<T>(
        IQueryable<T> source, CardParams p, string folder, Func<T, string, CardDto> toDto,
        List<int> orderedIds) where T : CardBase
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
        double? PriceOf(T c) => gradePrices != null
            ? (gradePrices.TryGetValue(c.Id, out var v) ? v : null)
            : c.NearMintPrice ?? c.MarketPrice;

        var fc = ParseForecastSort(p.OrderBy);
        var changes = fc is null ? null
            : await ForecastChanges(folder, ForecastTarget(p.Grade), fc.Value.horizon, matched.Select(c => c.Id).ToList());

        List<T> ordered;
        if (fc is { } f && changes != null)
        {
            double Key(T c) => changes.TryGetValue(c.Id, out var ch)
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
            .Select(c => toDto(c, ImageUrl(folder, c.Id)))
            .ToList();

        await ApplyGradePrice(cards, folder, p.Grade);
        if (fc is { } fs && changes != null)
            foreach (var card in cards)
                if (changes.TryGetValue(card.Id, out var ch)) SetExpected(card, ch, fs.metric, fs.horizon);
        return cards;
    }

    // The Owned list, expanded to one tile per (card + condition). Each tile carries
    // its quantity and the individual copies at that condition, is priced by that
    // condition's market price, and honors the same search/filter/sort/paging.
    private async Task<List<CardDto>> PageOwnedByCondition<T>(
        IQueryable<T> source, CardParams p, string folder, Func<T, string, CardDto> toDto)
        where T : CardBase
    {
        var user = User.Identity!.Name!;
        var game = GameKey(p.Game);

        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.Kind == TrackKind.Owned)
            .ToListAsync();

        // Card rows for the owned products, honoring search / set / rarity filters.
        var ids = copies.Select(x => x.ProductId).Distinct().ToList();
        var cardById = new Dictionary<int, T>();
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
        foreach (var tier in units.Select(u => PriceTier(u.Grade)).Distinct())
        {
            var tierIds = units.Where(u => PriceTier(u.Grade) == tier).Select(u => u.ProductId).Distinct().ToList();
            priceByTier[tier] = await GradePrices(folder, tier, tierIds);
        }
        double? UnitPrice(int pid, string grade) =>
            priceByTier[PriceTier(grade)].TryGetValue(pid, out var v) ? v : null;

        // Expected forecast change per unit, priced against the tile's own condition tier.
        var fc = ParseForecastSort(p.OrderBy);
        var changesByTarget = new Dictionary<string, Dictionary<int, (double pct, double usd, double from, double to)>>();
        if (fc is not null)
            foreach (var tgt in units.Select(u => ForecastTarget(u.Grade)).Distinct())
            {
                var tIds = units.Where(u => ForecastTarget(u.Grade) == tgt).Select(u => u.ProductId).Distinct().ToList();
                changesByTarget[tgt] = await ForecastChanges(folder, tgt, fc.Value.horizon, tIds);
            }
        (double pct, double usd, double from, double to)? UnitChange(int pid, string grade) =>
            changesByTarget.TryGetValue(ForecastTarget(grade), out var d) && d.TryGetValue(pid, out var ch) ? ch : null;
        double ChangeKey(int pid, string grade, (string metric, string horizon, bool desc) f) =>
            UnitChange(pid, grade) is { } ch
                ? (f.metric == "pct" ? ch.pct : ch.usd)
                : (f.desc ? double.NegativeInfinity : double.PositiveInfinity);  // no forecast -> end

        var ordered = (fc is { } fk
            ? (fk.desc
                ? units.OrderByDescending(u => ChangeKey(u.ProductId, u.Grade, fk))
                : units.OrderBy(u => ChangeKey(u.ProductId, u.Grade, fk)))
            : p.OrderBy switch
            {
                "price" => units.OrderBy(u => UnitPrice(u.ProductId, u.Grade)),
                "priceDesc" => units.OrderByDescending(u => UnitPrice(u.ProductId, u.Grade)),
                "name" => units.OrderBy(u => cardById[u.ProductId].Name),
                _ => units.OrderByDescending(u => u.LastAdded),   // order added
            }).ToList();

        Response.AddPaginationHeader(new PaginationMetadata
        {
            TotalCount = ordered.Count,
            PageSize = p.PageSize,
            CurrentPage = p.PageNumber,
            TotalPages = (int)Math.Ceiling(ordered.Count / (double)p.PageSize),
        });

        return ordered
            .Skip((p.PageNumber - 1) * p.PageSize)
            .Take(p.PageSize)
            .Select(u =>
            {
                var card = cardById[u.ProductId];
                var dto = toDto(card, ImageUrl(folder, card.Id));
                dto.Price = UnitPrice(u.ProductId, u.Grade);
                dto.OwnedGrade = u.Grade.Length == 0 ? null : u.Grade;
                dto.OwnedQuantity = u.Copies.Count;
                dto.OwnedCopies = u.Copies.Select(x => new OwnedCopyDto
                {
                    Id = x.Id,
                    Grade = x.Grade,
                    PurchasePrice = x.PurchasePrice,
                    AcquiredAt = x.AcquiredAt,
                    Note = x.Note,
                    AddedAt = x.AddedAt,
                }).ToList();
                if (fc is { } fp && UnitChange(u.ProductId, u.Grade) is { } chp)
                    SetExpected(dto, chp, fp.metric, fp.horizon);
                return dto;
            })
            .ToList();
    }

    // Owned copy-grade vocabulary -> price_history_unified tier. Near Mint and
    // unspecified fall back to the ungraded (Near Mint) series.
    private static string PriceTier(string grade) => grade is "" or "nm" ? "ungraded" : grade;

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

    private static async Task<object> Facets<T>(IQueryable<T> source) where T : CardBase
    {
        var sets = await source.Where(x => x.SetName != null)
            .Select(x => x.SetName!).Distinct().OrderBy(x => x).ToListAsync();
        var rarities = await source.Where(x => x.Rarity != null)
            .Select(x => x.Rarity!).Distinct().OrderBy(x => x).ToListAsync();

        return new { sets, rarities };
    }

    private string ImageUrl(string folder, int id) =>
        $"{Request.Scheme}://{Request.Host}/card-images/{folder}/{id}.jpg";

    private static bool IsPokemon(string? game) =>
        string.Equals(game?.Trim(), "pokemon", StringComparison.OrdinalIgnoreCase);

    private static string GameKey(string? game) => IsPokemon(game) ? "pokemon" : "onepiece";
}
