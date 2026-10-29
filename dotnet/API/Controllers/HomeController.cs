using API.Data;
using API.RequestHelpers;
using API.Extensions;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;

namespace API.Controllers;

// Numbers behind the home page's "at a glance" panels (2026-10-23): the
// model's public scorecard and where its 1-month calls lean, per game.
// Both change only when a data push swaps predictions.db, and every push
// restarts this process — cached for the process lifetime like the movers.
[Route("api/home")]
public partial class HomeController(PredictionsContext predictions, IMemoryCache cache) : BaseApiController
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
        Response.CacheForVisitor();
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

public partial class HomeController
{
    public record ModelScore(string Game, string Label, string Target, string Horizon,
                             long Graded, double BandHit, double Mae, double Bias, string? GradedThrough);
    public record WeekScore(string Week, long N, double? BandHit, double? Mae, double? FlatMae,
                            long? DirN, double? DirHit, long? N10, double? BandHit10, double? Mae10, double? FlatMae10);
    public record MonthScore(string Game, string Label, string Target, string Horizon, string Month,
                             long N, double? BandHit, double? Mae, double? Bias);
    public record Scorecard(List<ModelScore> Models, List<WeekScore> Weekly, List<MonthScore> Monthly,
                            string? UpdatedAt);

    // Every model's public record for the About page (2026-10-24): one row
    // per (game, tier, horizon) from forecast_accuracy, the full-cohort
    // weekly series of the 1-month Near Mint cohorts, and the persisted
    // monthly rows. The weekly table arrives with the first push after it
    // was introduced — a missing table reads as an empty series, never an
    // error. Cached for the process lifetime like the other home numbers.
    // The horizons the site scores. Anything else in the accuracy tables is
    // a retired experiment's residue (3m, retired 2026-10-24) and never
    // reaches the About page.
    private static readonly HashSet<string> ScoreHorizons = ["1m", "6m", "12m", "launch1m"];

    [HttpGet("scorecard")]
    public async Task<ActionResult<Scorecard>> GetScorecard()
    {
        var card = await cache.GetOrCreateAsync("home:scorecard", async entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromDays(7);
            entry.Priority = CacheItemPriority.NeverRemove;

            var models = (await predictions.Accuracy.Where(a => a.N > 0).ToListAsync())
                .Where(a => GameRegistry.Normalize(a.Game) != null && ScoreHorizons.Contains(a.Horizon))
                .Select(a => new ModelScore(a.Game, GameRegistry.Label(a.Game), a.Target, a.Horizon,
                                            a.N, a.BandHitRate, a.RetMae, a.RetBias, a.GradedThrough))
                .OrderBy(m => m.Label).ThenBy(m => m.Target).ThenBy(m => m.Horizon)
                .ToList();

            List<WeekScore> weekly = [];
            try
            {
                weekly = (await predictions.AccuracyWeekly.OrderBy(w => w.Week).ToListAsync())
                    .Select(w => new WeekScore(w.Week, w.N, w.BandHitRate, w.RetMae, w.FlatMae, w.DirN,
                                               w.DirHitRate, w.N10, w.BandHitRate10, w.RetMae10, w.FlatMae10))
                    .ToList();
            }
            catch (Microsoft.Data.Sqlite.SqliteException) { /* table not pushed yet */ }

            List<MonthScore> monthly = [];
            try
            {
                monthly = (await predictions.AccuracyMonthly.ToListAsync())
                    .Where(m => GameRegistry.Normalize(m.Game) != null && ScoreHorizons.Contains(m.Horizon))
                    .Select(m => new MonthScore(m.Game, GameRegistry.Label(m.Game), m.Target, m.Horizon,
                                                m.Month, m.N, m.BandHitRate, m.RetMae, m.RetBias))
                    .OrderBy(m => m.Month).ThenBy(m => m.Label)
                    .ToList();
            }
            catch (Microsoft.Data.Sqlite.SqliteException) { }

            var updated = models.Select(m => m.GradedThrough).Where(d => d != null).Max();
            return new Scorecard(models, weekly, monthly, updated);
        });
        return Ok(card);
    }
}

file static class AccuracyExt
{
    public static double BandHit(this API.Entities.ForecastAccuracy a) => a.BandHitRate;
}
