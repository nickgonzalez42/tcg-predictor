using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Weekly TCGplayer sales for one card (2026-11-09, user: "add a volume table
// to the card detail page" and the sold bars on its chart). Source: the
// daytime sales crawl's tcg_sales_history, which holds about a year of weekly
// buckets per variant and condition for every priced card, refreshed on a
// 28-day rotation. The page shows the printing it is on, Near Mint (the
// condition the headline price tracks), with every condition summed alongside.
public partial class CardsController
{
    private const string NearMint = "Near Mint";

    [HttpGet("{game}/{id:int}/sales")]
    public async Task<IActionResult> GetSales(string game, int id, [FromQuery] string? printing = null)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var rows = await priceCharting.Sales
            .Where(s => s.Game == key && s.ProductId == id)
            .ToListAsync();
        if (rows.Count == 0)
            return Ok(new SalesDto(key, id, null, NearMint, null, []));

        // The crawl names variants the way TCGplayer does, which is also where
        // the catalog's printing names come from (printing_ingest), so the
        // shown printing matches a variant directly. A card with no recorded
        // base printing, or whose name differs, falls back to the variant
        // with the most Near Mint copies sold.
        var card = await sources.Find(key, id);
        var variant = !string.IsNullOrEmpty(printing) ? printing : card?.BasePrinting;
        var variants = rows.Select(r => r.Variant).Distinct().ToList();
        if (variant == null || !variants.Contains(variant))
            variant = rows.Where(r => r.Condition == NearMint)
                .GroupBy(r => r.Variant)
                .OrderByDescending(g => g.Sum(r => r.QuantitySold ?? 0))
                .Select(g => g.Key)
                .FirstOrDefault() ?? variants[0];

        // The crawl occasionally stores a bucket dated into the future (the
        // chart's week boundaries); nothing past today is served.
        var today = DateTime.UtcNow.ToString("yyyy-MM-dd");
        var weeks = rows.Where(r => r.Variant == variant && string.Compare(r.BucketDate, today) <= 0)
            .GroupBy(r => r.BucketDate)
            .OrderBy(g => g.Key)
            .Select(g =>
            {
                var nm = g.FirstOrDefault(r => r.Condition == NearMint);
                return new SalesWeekDto(
                    g.Key,
                    Math.Round(nm?.QuantitySold ?? 0),
                    Math.Round(nm?.TransactionCount ?? 0),
                    nm?.LowSale is > 0 ? nm.LowSale : null,
                    nm?.HighSale is > 0 ? nm.HighSale : null,
                    nm?.MarketPrice is > 0 ? nm.MarketPrice : null,
                    Math.Round(g.Sum(r => r.QuantitySold ?? 0)));
            })
            .ToList();

        var scan = await priceCharting.SalesScans
            .FirstOrDefaultAsync(s => s.Game == key && s.ProductId == id);
        var asOf = scan?.FetchedAt is { Length: >= 10 } f ? f[..10] : null;
        return Ok(new SalesDto(key, id, variant, NearMint, asOf, weeks));
    }

    public record SalesWeekDto(
        string WeekStart,          // the bucket's first day
        double Sold,               // Near Mint copies sold that week
        double Orders,             // orders those copies came from
        double? Low,               // lowest / highest Near Mint sale that week
        double? High,
        double? Market,            // TCGplayer market price at the time
        double SoldAllConditions); // copies sold across every condition

    public record SalesDto(
        string Game, int ProductId,
        string? Variant,           // the TCGplayer printing the figures are for
        string Condition,
        string? AsOf,              // the day the crawl last fetched this card (YYYY-MM-DD)
        List<SalesWeekDto> Weeks); // oldest first; the newest is the week in progress at AsOf
}
