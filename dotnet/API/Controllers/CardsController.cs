using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class CardsController(
    OnePieceContext onePiece, PokemonContext pokemon,
    PredictionsContext predictions, PriceChartingContext priceCharting) : BaseApiController
{
    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        return IsPokemon(cardParams.Game)
            ? await Page(pokemon.Cards, cardParams, "pokemon", (c, url) => c.ToDto(url))
            : await Page(onePiece.Cards, cardParams, "onepiece", (c, url) => c.ToDto(url));
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
        return dto;
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
            f.ForecastPrice, f.Low, f.High, f.Ret,
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
        var query = source
            .Sort(cardParams.OrderBy)
            .Search(cardParams.SearchTerm)
            .Filter(cardParams.Sets, cardParams.Rarities);

        var paged = await PagedList<T>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = paged.Select(c => toDto(c, ImageUrl(folder, c.Id))).ToList();
        await ApplyGradePrice(cards, folder, cardParams.Grade);
        return cards;
    }

    // When a grade/condition tier is selected, show that tier's latest price
    // (same source as the forecast "Current" — price_history_unified) instead of
    // the default TCGplayer market price.
    private async Task ApplyGradePrice(List<CardDto> cards, string game, string? grade)
    {
        if (string.IsNullOrEmpty(grade) || cards.Count == 0) return;

        var ids = cards.Select(c => c.Id).ToList();
        var rows = await priceCharting.History
            .Where(h => h.Game == game && h.Grade == grade && ids.Contains(h.ProductId))
            .Select(h => new { h.ProductId, h.Date, h.Price })
            .ToListAsync();

        var latest = rows
            .GroupBy(r => r.ProductId)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(r => r.Date).First().Price);

        foreach (var card in cards)
            card.Price = latest.TryGetValue(card.Id, out var p) ? p : null;
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
