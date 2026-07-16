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
// forecast kinds). The list endpoint returns each alert with its current value
// and hit state, evaluated by AlertEvaluator (shared with the email notifier).
[Authorize]
public class AlertsController(
    StoreContext store, CardSources sources, AlertEvaluator evaluator) : BaseApiController
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

        var evaluated = await evaluator.EvaluateAsync(alerts);

        // Card names/art for the notifications page rows (one query per game).
        var cardByKey = new Dictionary<(string, int), (string? Name, string? SetName)>();
        foreach (var g in alerts.Select(a => a.Game).Distinct())
        {
            var ids = alerts.Where(a => a.Game == g).Select(a => a.ProductId).Distinct().ToList();
            foreach (var c in await sources.Cards(g)
                         .Where(c => ids.Contains(c.Id))
                         .Select(c => new { c.Id, c.Name, c.SetName }).ToListAsync())
                cardByKey[(g, c.Id)] = (c.Name, c.SetName);
        }

        return Ok(evaluated.Select(e => new
        {
            e.Alert.Id, e.Alert.Game, e.Alert.ProductId, e.Alert.Grade, e.Alert.Kind,
            e.Alert.Horizon, e.Alert.Direction, e.Alert.Target,
            e.Current, e.Hit, e.Alert.CreatedAt,
            cardByKey.GetValueOrDefault((e.Alert.Game, e.Alert.ProductId)).Name,
            cardByKey.GetValueOrDefault((e.Alert.Game, e.Alert.ProductId)).SetName,
            PictureUrl = CardImageUrl(e.Alert.Game, e.Alert.ProductId),
        }));
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
}
