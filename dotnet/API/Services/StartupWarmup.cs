using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;

namespace API.Services;

// Self-warm the homepage's hot paths after every restart (2026-08-27): each
// nightly data push and each deploy restarts the API, which empties the
// movers cache, and the ranking recompute costs ~25s over the multi-GB DBs —
// a cost the first visitor of the day kept paying (cold-homepage reports
// 2026-08-14 and 2026-08-27). External curl warmups only ever covered the
// scripts that remembered to run them; this runs on every boot, firing the
// exact requests the home page makes through the real HTTP pipeline, so the
// cache keys match to the byte.
public class StartupWarmup(IServer server, IHostApplicationLifetime lifetime,
                           ILogger<StartupWarmup> logger) : BackgroundService
{
    // Keep these byte-identical to the requests the client makes (the cache
    // keys are built from the normalized parameters). 2026-10-25: the ticker
    // and hero keys had drifted (count=12 / perGame=4), so the warm-up was
    // priming entries nobody read; the per-game facets were never warmed and
    // cost 14-43 s cold on the big games — the catalog page blocks on them.
    private static readonly string[] Paths =
    [
        "/api/cards/movers?game=all&count=10",             // market ticker
        "/api/cards/movers?count=24&horizon=1m&trend=6m",  // homepage mover tiles
        "/api/cards/movers?horizon=mix&perGame=2",         // featured-card hero
        "/api/home/stats",                                 // home glance panels
        "/api/home/scorecard",                             // About page
        .. API.RequestHelpers.GameRegistry.Keys.SelectMany(g => new[]
        {
            $"/api/cards/filters?game={g}",                // catalog facets (cached per process)
            $"/api/cards?game={g}&pageSize=30",            // first catalog page (pulls pages into the OS cache)
            $"/api/cards?game={g}&pageSize=30&orderBy=histPct6mDesc",  // past-change sort: loads the game's trends (CardMarketData.TierTrends)
        }),
    ];

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        // Belt and braces: NOTHING a cache warmer does may stop the host.
        try { await WarmAsync(ct); }
        catch (Exception e) when (!ct.IsCancellationRequested)
        {
            logger.LogWarning("warmup aborted: {Err}", e.Message);
        }
    }

    private async Task WarmAsync(CancellationToken ct)
    {
        // ApplicationStarted is a CancellationToken: Register fires
        // immediately if the app is already up, so this never deadlocks.
        var started = new TaskCompletionSource();
        await using var reg = lifetime.ApplicationStarted.Register(() => started.TrySetResult());
        await started.Task.WaitAsync(ct);

        var address = server.Features.Get<IServerAddressesFeature>()?.Addresses.FirstOrDefault();
        if (address is null)
        {
            logger.LogWarning("warmup: no server address found — skipped");
            return;
        }
        var baseUrl = address.Replace("[::]", "127.0.0.1")
                             .Replace("0.0.0.0", "127.0.0.1")
                             .Replace("+", "127.0.0.1");
        // 10 min: a warmup that outlives this is slow, not broken — and a
        // warmup timeout must NEVER become a host stop (see catch below).
        using var http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromMinutes(10) };
        foreach (var p in Paths)
        {
            if (ct.IsCancellationRequested) return;
            try
            {
                var t0 = DateTime.UtcNow;
                using var r = await http.GetAsync(p, ct);
                logger.LogInformation("warmup {Path}: {Status} in {Secs:F1}s",
                                      p, (int)r.StatusCode, (DateTime.UtcNow - t0).TotalSeconds);
            }
            // 2026-10-11 outage: HttpClient's TIMEOUT throws TaskCanceled-
            // Exception — an OperationCanceledException — so the old filter
            // (`is not OperationCanceledException`, meant for host shutdown)
            // let it escape, and BackgroundServiceExceptionBehavior=StopHost
            // killed the API: a slow movers recompute became a crash loop
            // (boot → warmup times out → host stops → colder boot → slower
            // recompute → ...). Only a genuine shutdown may pass through.
            catch (Exception e) when (!ct.IsCancellationRequested)
            {
                logger.LogWarning("warmup {Path} failed: {Err}", p, e.Message);
            }
        }
    }
}
