using API.Data;
using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Card alerts: several per card, each on the current price, a forecast price,
// or a forecast % change — scoped to a condition tier (and a horizon for the
// forecast kinds). The list endpoint evaluates each alert's current value so
// the client can show live "hit" states without re-deriving pricing rules.
[Authorize]
public class AlertsController(
    StoreContext store, CardSources sources,
    PredictionsContext predictions, PriceChartingContext priceCharting) : BaseApiController
{
    public record CreateAlertDto(
        string Game, int ProductId, string? Grade, string Kind,
        string? Horizon, string Direction, double Target);

    private static readonly HashSet<string> Kinds =
        [AlertKind.Price, AlertKind.ForecastPrice, AlertKind.ForecastPct];
    // New alerts follow the site's served horizons (no 1w — a legacy 1w alert
    // still displays and evaluates, but can't be created).
    private static readonly HashSet<string> Horizons = ["1m", "6m", "12m"];
    private const int MaxPerCard = 10;

    [HttpGet]
    public async Task<IActionResult> List()
    {
        var user = User.Identity!.Name!;
        var alerts = await store.CardAlerts
            .Where(a => a.UserName == user)
            .OrderBy(a => a.CreatedAt)
            .ToListAsync();

        var results = new List<object>();
        foreach (var group in alerts.GroupBy(a => a.Game))
        {
            var game = group.Key;
            var ids = group.Select(a => a.ProductId).Distinct().ToList();
            var pricedById = (await priceCharting.GradedPrices
                    .Where(p => p.Game == game && ids.Contains(p.ProductId)).ToListAsync())
                .ToDictionary(p => p.ProductId);
            var fcByKey = (await predictions.Forecasts
                    .Where(f => f.Game == game && ids.Contains(f.ProductId))
                    .Select(f => new { f.ProductId, f.Target, f.Horizon, f.BasePrice, f.ForecastPrice })
                    .ToListAsync())
                .ToDictionary(f => (f.ProductId, f.Target, f.Horizon));

            foreach (var a in group)
            {
                double? current = null;
                if (a.Kind == AlertKind.Price)
                {
                    current = TierPrice(pricedById.GetValueOrDefault(a.ProductId), a.Grade);
                }
                else if (fcByKey.TryGetValue(
                             (a.ProductId, GradeTiers.ForecastTarget(a.Grade), a.Horizon ?? ""), out var f)
                         && f.ForecastPrice is { } fp)
                {
                    current = a.Kind == AlertKind.ForecastPrice
                        ? fp
                        : f.BasePrice > 0 ? (fp / f.BasePrice - 1) * 100 : null;
                }

                var hit = current != null
                          && (a.Direction == "above" ? current >= a.Target : current <= a.Target);
                results.Add(new
                {
                    a.Id, a.Game, a.ProductId, a.Grade, a.Kind, a.Horizon,
                    a.Direction, a.Target, Current = current, Hit = hit, a.CreatedAt,
                });
            }
        }
        return Ok(results);
    }

    [HttpPost]
    public async Task<IActionResult> Create(CreateAlertDto dto)
    {
        var user = User.Identity!.Name!;
        var game = GameRegistry.Normalize(dto.Game);
        if (game == null) return BadRequest($"Unknown game '{dto.Game}'.");

        var kind = dto.Kind?.Trim().ToLowerInvariant() ?? "";
        if (!Kinds.Contains(kind)) return BadRequest("Unknown alert kind.");

        var grade = string.IsNullOrWhiteSpace(dto.Grade) || dto.Grade == "ungraded"
            ? null : dto.Grade.Trim().ToLowerInvariant();
        if (grade != null && !GradeTiers.Graded.Contains(grade))
            return BadRequest($"Unknown condition '{dto.Grade}'.");

        string? horizon = null;
        if (kind != AlertKind.Price)
        {
            horizon = dto.Horizon?.Trim().ToLowerInvariant();
            if (horizon == null || !Horizons.Contains(horizon))
                return BadRequest("Forecast alerts need a horizon (1m, 6m, 12m).");
        }

        var direction = dto.Direction?.Trim().ToLowerInvariant();
        if (direction is not ("above" or "below")) return BadRequest("Direction must be above or below.");

        // Prices are positive dollars; a forecast % target may be negative
        // ("alert me if the 6M outlook drops below −20%") but stays sane.
        if (!double.IsFinite(dto.Target)) return BadRequest("Bad target value.");
        if (kind != AlertKind.ForecastPct && dto.Target <= 0)
            return BadRequest("Price targets must be positive.");
        if (kind == AlertKind.ForecastPct && Math.Abs(dto.Target) > 1000)
            return BadRequest("Percent targets must be within ±1000.");

        if (await sources.Find(game, dto.ProductId) == null) return NotFound("Card not found.");

        var count = await store.CardAlerts.CountAsync(
            a => a.UserName == user && a.Game == game && a.ProductId == dto.ProductId);
        if (count >= MaxPerCard) return BadRequest($"At most {MaxPerCard} alerts per card.");

        var alert = new CardAlert
        {
            UserName = user, Game = game, ProductId = dto.ProductId, Grade = grade,
            Kind = kind, Horizon = horizon, Direction = direction, Target = dto.Target,
        };
        store.CardAlerts.Add(alert);
        await store.SaveChangesAsync();
        return Ok(new { alert.Id });
    }

    [HttpDelete("{id:int}")]
    public async Task<IActionResult> Delete(int id)
    {
        var alert = await store.CardAlerts.FirstOrDefaultAsync(
            a => a.Id == id && a.UserName == User.Identity!.Name);
        if (alert == null) return NotFound();
        store.CardAlerts.Remove(alert);
        await store.SaveChangesAsync();
        return Ok();
    }

    // The tier's current price from the snapshot table (null = no data).
    private static double? TierPrice(GradedPrice? p, string? grade) => p == null ? null : grade switch
    {
        null or "" => p.Ungraded,
        "grade7" => p.Grade7,
        "grade8" => p.Grade8,
        "grade9" => p.Grade9,
        "grade95" => p.Grade95,
        "psa10" => p.Psa10,
        "bgs10" => p.Bgs10,
        "cgc10" => p.Cgc10,
        "sgc10" => p.Sgc10,
        _ => p.Ungraded,
    };
}
