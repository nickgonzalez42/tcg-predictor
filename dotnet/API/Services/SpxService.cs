using System.Text.Json;
using API.Data;
using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Daily S&P 500 closes for the portfolio's "same $ in the market" benchmark.
// Cached in store.db (SpxCloses) and topped up from Yahoo Finance's public
// chart endpoint — best-effort: a failed refresh just serves what's cached.
public class SpxService(StoreContext store, HttpClient http)
{
    private const string Url =
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=2y&interval=1d";

    // One refresh at a time across requests, and at most one network attempt
    // per half day (markets are closed most of the time anyway).
    private static readonly SemaphoreSlim Gate = new(1, 1);
    private static DateTime _lastAttemptUtc = DateTime.MinValue;

    public async Task<List<SpxClose>> GetCloses(string fromDate)
    {
        await RefreshIfStale();
        return await store.SpxCloses
            .Where(x => string.Compare(x.Date, fromDate) >= 0)
            .OrderBy(x => x.Date)
            .ToListAsync();
    }

    private async Task RefreshIfStale()
    {
        // Fresh enough once we hold yesterday's close (weekends/holidays make
        // the latest close legitimately older; the attempt window covers that).
        var latest = await store.SpxCloses.OrderByDescending(x => x.Date)
            .Select(x => x.Date).FirstOrDefaultAsync();
        var target = DateTime.UtcNow.AddDays(-1).ToString("yyyy-MM-dd");
        if (latest != null && string.CompareOrdinal(latest, target) >= 0) return;
        if (DateTime.UtcNow - _lastAttemptUtc < TimeSpan.FromHours(12)) return;

        await Gate.WaitAsync();
        try
        {
            if (DateTime.UtcNow - _lastAttemptUtc < TimeSpan.FromHours(12)) return;
            _lastAttemptUtc = DateTime.UtcNow;

            using var req = new HttpRequestMessage(HttpMethod.Get, Url);
            req.Headers.UserAgent.ParseAdd("Mozilla/5.0");   // Yahoo rejects UA-less requests
            using var resp = await http.SendAsync(req);
            resp.EnsureSuccessStatusCode();

            using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
            var result = doc.RootElement.GetProperty("chart").GetProperty("result")[0];
            var stamps = result.GetProperty("timestamp");
            var closes = result.GetProperty("indicators").GetProperty("quote")[0]
                .GetProperty("close");

            var have = (await store.SpxCloses.AsNoTracking().ToListAsync())
                .ToDictionary(x => x.Date);
            for (var i = 0; i < stamps.GetArrayLength(); i++)
            {
                if (closes[i].ValueKind != JsonValueKind.Number) continue;  // holiday nulls
                var date = DateTimeOffset.FromUnixTimeSeconds(stamps[i].GetInt64())
                    .UtcDateTime.ToString("yyyy-MM-dd");
                var close = closes[i].GetDouble();
                if (have.TryGetValue(date, out var row))
                {
                    if (Math.Abs(row.Close - close) > 0.001)   // today's point moves intraday
                    {
                        row.Close = close;
                        store.SpxCloses.Update(row);
                    }
                }
                else
                {
                    store.SpxCloses.Add(new SpxClose { Date = date, Close = close });
                }
            }
            await store.SaveChangesAsync();
        }
        catch
        {
            // Benchmark is decorative: keep serving the cached closes.
        }
        finally
        {
            Gate.Release();
        }
    }
}
