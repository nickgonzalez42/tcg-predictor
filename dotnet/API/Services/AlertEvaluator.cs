using API.Data;
using API.Entities;
using API.RequestHelpers;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Evaluates card alerts against today's data: the tier's current snapshot
// price for price alerts, the live forecast table for the forecast kinds.
// Shared by the alerts API (read-time hit flags) and the email notifier.
public class AlertEvaluator(PredictionsContext predictions, PriceChartingContext priceCharting)
{
    public record Evaluated(CardAlert Alert, double? Current, bool Hit);

    public async Task<List<Evaluated>> EvaluateAsync(List<CardAlert> alerts)
    {
        var results = new List<Evaluated>();
        foreach (var group in alerts.GroupBy(a => a.Game))
        {
            var game = group.Key;
            var ids = group.Select(a => a.ProductId).Distinct().ToList();
            var pricedById = (await priceCharting.GradedPrices
                    .Where(p => p.Game == game && ids.Contains(p.ProductId)).ToListAsync())
                .ToDictionary(p => p.ProductId);
            var fcByKey = (await predictions.Forecasts.IgnoreQueryFilters()
                    .Where(f => f.Game == game && ids.Contains(f.ProductId))
                    .Select(f => new { f.ProductId, f.Printing, f.Target, f.Horizon, f.BasePrice, f.ForecastPrice })
                    .ToListAsync())
                .ToDictionary(f => (f.ProductId, f.Printing, f.Target, f.Horizon));
            // Labeled-printing price alerts read the printing's own series
            // (there is no snapshot table for printings).
            var printedIds = group.Where(a => a.Printing != "").Select(a => a.ProductId).Distinct().ToList();
            var printedPrices = printedIds.Count == 0 ? [] :
                (await priceCharting.History.IgnoreQueryFilters()
                    .Where(h => h.Game == game && printedIds.Contains(h.ProductId) && h.Printing != "")
                    .Select(h => new { h.ProductId, h.Printing, h.Grade, h.Date, h.Price })
                    .ToListAsync())
                .GroupBy(h => (h.ProductId, h.Printing, h.Grade))
                .ToDictionary(g => g.Key, g => g.OrderByDescending(r => r.Date).First().Price);

            foreach (var a in group)
            {
                double? current = null;
                if (a.Kind == AlertKind.Price)
                {
                    current = a.Printing == ""
                        ? pricedById.GetValueOrDefault(a.ProductId)?.PriceFor(a.Grade)
                        : printedPrices.GetValueOrDefault(
                              (a.ProductId, a.Printing, GradeTiers.PriceTier(a.Grade)));
                }
                else if (fcByKey.TryGetValue(
                             (a.ProductId, a.Printing, GradeTiers.ForecastTarget(a.Grade), a.Horizon ?? ""), out var f)
                         && f.ForecastPrice is { } fp)
                {
                    current = a.Kind == AlertKind.ForecastPrice
                        ? fp
                        : f.BasePrice > 0 ? (fp / f.BasePrice - 1) * 100 : null;
                }

                var hit = current != null
                          && (a.Direction == "above" ? current >= a.Target : current <= a.Target);
                results.Add(new Evaluated(a, current, hit));
            }
        }
        return results;
    }
}
