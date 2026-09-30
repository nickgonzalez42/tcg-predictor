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
    private static readonly string[] Paths =
    [
        "/api/cards/movers?count=12",                      // market ticker
        "/api/cards/movers?count=24&horizon=1m&trend=6m",  // homepage movers grid
        "/api/cards/movers?horizon=mix&perGame=4",         // hero scenes
        "/api/cards?game=pokemon&pageSize=30",             // default catalog page
    ];

    protected override async Task ExecuteAsync(CancellationToken ct)
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
        using var http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromMinutes(3) };
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
            catch (Exception e) when (e is not OperationCanceledException)
            {
                logger.LogWarning("warmup {Path} failed: {Err}", p, e.Message);
            }
        }
    }
}
