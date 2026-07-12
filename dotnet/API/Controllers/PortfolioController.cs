using API.Data;
using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Brokerage-style rollup of the signed-in user's owned copies: total value,
// a monthly value-over-time series, allocation, and best/worst positions.
// Prices come from price_history_unified per condition tier (monthly points),
// so "day" granularity doesn't exist — the change figures are month-over-month.
[Authorize]
public class PortfolioController(
    StoreContext store, PriceChartingContext priceCharting, CardSources sources) : BaseApiController
{
    private static readonly Dictionary<string, string> TierLabels = new()
    {
        ["ungraded"] = "Ungraded", ["grade7"] = "Grade 7", ["grade8"] = "Grade 8",
        ["grade9"] = "Grade 9", ["grade95"] = "Grade 9.5", ["psa10"] = "PSA 10",
        ["bgs10"] = "BGS 10", ["cgc10"] = "CGC 10", ["sgc10"] = "SGC 10",
    };

    private static string TierLabel(string tier) => TierLabels.GetValueOrDefault(tier, tier);

    [HttpGet("summary")]
    public async Task<IActionResult> GetSummary()
    {
        var user = User.Identity!.Name!;
        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Owned)
            .ToListAsync();
        if (copies.Count == 0)
            return Ok(new { totalValue = 0.0, copies = 0 });

        // Full monthly history for every (game, product, tier) the portfolio touches.
        var seriesByKey = new Dictionary<(string Game, int Id, string Tier), List<(string Date, double Price)>>();
        foreach (var gameGroup in copies.GroupBy(c => c.Game))
        {
            var ids = gameGroup.Select(c => c.ProductId).Distinct().ToList();
            var tiers = gameGroup.Select(c => GradeTiers.PriceTier(c.Grade)).Distinct().ToList();
            var rows = await priceCharting.History
                .Where(h => h.Game == gameGroup.Key && ids.Contains(h.ProductId) && tiers.Contains(h.Grade))
                .Select(h => new { h.ProductId, h.Grade, h.Date, h.Price })
                .ToListAsync();
            foreach (var g in rows.GroupBy(r => (r.ProductId, r.Grade)))
                seriesByKey[(gameGroup.Key, g.Key.ProductId, g.Key.Grade)] =
                    g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList();
        }

        List<(string Date, double Price)>? SeriesOf(TrackedCard c) =>
            seriesByKey.GetValueOrDefault((c.Game, c.ProductId, GradeTiers.PriceTier(c.Grade)));
        double? LatestOf(TrackedCard c) =>
            SeriesOf(c) is { Count: > 0 } s ? s[^1].Price : null;

        // ----- Headline: total value + allocation -----
        var totalValue = copies.Sum(c => LatestOf(c) ?? 0);

        // Two views of the same value: by game, and by condition/grade tier.
        List<object> Breakdown(Func<TrackedCard, string> label) => copies
            .GroupBy(label)
            .Select(g => new { label = g.Key, value = g.Sum(c => LatestOf(c) ?? 0) })
            .Where(a => a.value > 0)
            .OrderByDescending(a => a.value)
            .Select(a => (object)new
            {
                a.label,
                value = Math.Round(a.value, 2),
                pct = totalValue > 0 ? Math.Round(a.value / totalValue * 100, 1) : 0.0,
            })
            .ToList();

        var allocation = Breakdown(c => GameRegistry.Label(c.Game));
        var gradeAllocation = Breakdown(c => TierLabel(GradeTiers.PriceTier(c.Grade)));

        // ----- Value over time (last 24 monthly points, prices carried forward) -----
        var dates = seriesByKey.Values.SelectMany(s => s.Select(p => p.Date))
            .Distinct().OrderBy(d => d).TakeLast(24).ToList();

        // One forward pass: dates are ascending, so each copy just advances a
        // cursor through its own date-sorted series (prices carry forward).
        var cursors = copies
            .Select(c => (Series: SeriesOf(c), Idx: -1))
            .ToArray();
        var series = new List<(string Date, double Value)>(dates.Count);
        foreach (var date in dates)
        {
            double total = 0;
            for (var i = 0; i < cursors.Length; i++)
            {
                var s = cursors[i].Series;
                if (s == null) continue;
                while (cursors[i].Idx + 1 < s.Count &&
                       string.CompareOrdinal(s[cursors[i].Idx + 1].Date, date) <= 0)
                    cursors[i].Idx++;
                if (cursors[i].Idx >= 0) total += s[cursors[i].Idx].Price;
            }
            series.Add((date, Math.Round(total, 2)));
        }

        double? monthChangeUsd = null, monthChangePct = null;
        if (series.Count >= 2 && series[^2].Value > 0)
        {
            monthChangeUsd = Math.Round(series[^1].Value - series[^2].Value, 2);
            monthChangePct = Math.Round((series[^1].Value / series[^2].Value - 1) * 100, 1);
        }

        // ----- P/L vs what was paid (only copies with a purchase price) -----
        var paidCopies = copies.Where(c => c.PurchasePrice is > 0).ToList();
        var paid = paidCopies.Sum(c => c.PurchasePrice!.Value);
        var paidValue = paidCopies.Sum(c => LatestOf(c) ?? 0);
        object? allTime = paid > 0 ? new
        {
            paid = Math.Round(paid, 2),
            value = Math.Round(paidValue, 2),
            plUsd = Math.Round(paidValue - paid, 2),
            plPct = Math.Round((paidValue / paid - 1) * 100, 1),
        } : null;

        // ----- Best / worst position (per card+tier, needs paid data) -----
        var positions = paidCopies
            .GroupBy(c => (c.Game, c.ProductId, Tier: GradeTiers.PriceTier(c.Grade)))
            .Select(g => new
            {
                g.Key.Game,
                Id = g.Key.ProductId,
                Paid = g.Sum(c => c.PurchasePrice!.Value),
                Value = g.Sum(c => LatestOf(c) ?? 0),
            })
            .Where(p => p.Paid > 0 && p.Value > 0)
            .Select(p => (p.Game, p.Id, p.Paid, p.Value,
                          Pct: Math.Round((p.Value / p.Paid - 1) * 100, 1)))
            .OrderByDescending(p => p.Pct)
            .ToList();

        async Task<object?> Position((string Game, int Id, double Paid, double Value, double Pct)? p)
        {
            if (p is not { } pos) return null;
            var card = await sources.Find(pos.Game, pos.Id);
            return new
            {
                game = pos.Game,
                id = pos.Id,
                name = card?.Name,
                pictureUrl = CardImageUrl(pos.Game, pos.Id),
                pct = pos.Pct,
                paid = Math.Round(pos.Paid, 2),
                value = Math.Round(pos.Value, 2),
                plUsd = Math.Round(pos.Value - pos.Paid, 2),
            };
        }

        return Ok(new
        {
            totalValue = Math.Round(totalValue, 2),

            copies = copies.Count,
            monthChangeUsd,
            monthChangePct,
            allTime,
            allocation,
            gradeAllocation,
            best = await Position(positions.Count > 0 ? positions[0] : null),
            worst = await Position(positions.Count > 1 ? positions[^1] : null),
            series = series.Select(p => new { date = p.Date, value = p.Value }),
        });
    }
}
