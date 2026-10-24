using API.Data;
using API.RequestHelpers;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;

namespace API.Controllers;

// Numbers behind the home page's "at a glance" panels (2026-10-23): the
// model's public scorecard and where its 1-month calls lean, per game.
// Both change only when a data push swaps predictions.db, and every push
// restarts this process — cached for the process lifetime like the movers.
[Route("api/home")]
public class HomeController(PredictionsContext predictions, IMemoryCache cache) : BaseApiController
{
    public record GameHit(string Game, string Label, long Graded, double BandHit);
    public record GameLean(string Game, string Label, int Up, int Flat, int Down);
    // Mae/Bias are mean log-return errors: Mae = typical size of the miss,
    // Bias = mean (predicted - realized), positive when the model runs hot.
    public record GameMiss(string Game, string Label, long Graded, double Mae, double Bias);
    public record HomeStats(string Horizon, long Graded, double BandHit, string? GradedThrough,
                            List<GameHit> ByGame, List<GameLean> Lean,
                            double Mae, double Bias, List<GameMiss> Miss);

    [HttpGet("stats")]
    public async Task<ActionResult<HomeStats>> Stats()
    {
        var stats = await cache.GetOrCreateAsync("home:stats", async entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromDays(7);
            entry.Priority = CacheItemPriority.NeverRemove;

            // Scorecard: Near Mint, 1 month — the only horizon with a graded
            // record deep enough to show (6m/12m have a handful of rows so far).
            var acc = await predictions.Accuracy
                .Where(a => a.Target == "ungraded" && a.Horizon == "1m" && a.N > 0)
                .ToListAsync();
            var byGame = acc
                .Where(a => GameRegistry.Normalize(a.Game) != null)
                .Select(a => new GameHit(a.Game, GameRegistry.Label(a.Game), a.N, a.BandHit()))
                .OrderByDescending(g => g.BandHit)
                .ToList();
            var graded = acc.Sum(a => a.N);
            var bandHit = graded > 0 ? acc.Sum(a => a.N * a.BandHitRate) / graded : 0.0;
            var through = acc.Select(a => a.GradedThrough).Where(d => d != null).Max();
            // How far off (2026-10-23): the typical miss and its lean, per game.
            var miss = acc
                .Where(a => GameRegistry.Normalize(a.Game) != null)
                .Select(a => new GameMiss(a.Game, GameRegistry.Label(a.Game), a.N, a.RetMae, a.RetBias))
                .OrderBy(g => g.Mae)
                .ToList();
            var mae = graded > 0 ? acc.Sum(a => a.N * a.RetMae) / graded : 0.0;
            var bias = graded > 0 ? acc.Sum(a => a.N * a.RetBias) / graded : 0.0;

            // Lean: how many of each game's base-printing cards the 1m model has
            // up, unchanged or down (the global filter already keeps base rows).
            var lean = (await predictions.Forecasts
                    .Where(f => f.Target == "ungraded" && f.Horizon == "1m" && f.BasePrice > 0)
                    .GroupBy(f => f.Game)
                    .Select(g => new
                    {
                        Game = g.Key,
                        Up = g.Count(f => f.ForecastPrice > f.BasePrice),
                        Flat = g.Count(f => f.ForecastPrice == f.BasePrice),
                        Down = g.Count(f => f.ForecastPrice < f.BasePrice),
                    })
                    .ToListAsync())
                .Where(g => GameRegistry.Normalize(g.Game) != null)
                .Select(g => new GameLean(g.Game, GameRegistry.Label(g.Game), g.Up, g.Flat, g.Down))
                .OrderByDescending(g => (double)g.Up / Math.Max(1, g.Up + g.Flat + g.Down))
                .ToList();

            return new HomeStats("1m", graded, bandHit, through, byGame, lean, mae, bias, miss);
        });
        return Ok(stats);
    }
}

file static class AccuracyExt
{
    public static double BandHit(this API.Entities.ForecastAccuracy a) => a.BandHitRate;
}
