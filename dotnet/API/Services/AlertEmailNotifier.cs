using API.Data;
using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Emails opted-in users when a card alert crosses into "hit". Runs a few
// minutes after startup — the daily data push restarts the API, so every
// fresh dataset gets a pass — then every 6 hours as a safety net.
// Edge-triggered per alert: NotifiedAt stamps a sent hit (so it isn't
// re-sent daily while it stays hit) and clears when the alert un-hits,
// re-arming it for the next crossing.
public class AlertEmailNotifier(
    IServiceScopeFactory scopes, EmailService email,
    IConfiguration config, ILogger<AlertEmailNotifier> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        await Task.Delay(TimeSpan.FromMinutes(3), ct);
        while (!ct.IsCancellationRequested)
        {
            try { await RunPass(ct); }
            catch (Exception ex) { logger.LogWarning(ex, "Alert email pass failed"); }
            await Task.Delay(TimeSpan.FromHours(6), ct);
        }
    }

    private async Task RunPass(CancellationToken ct)
    {
        using var scope = scopes.CreateScope();
        var store = scope.ServiceProvider.GetRequiredService<StoreContext>();
        var evaluator = scope.ServiceProvider.GetRequiredService<AlertEvaluator>();
        var sources = scope.ServiceProvider.GetRequiredService<CardSources>();

        var emailByUser = await store.Users
            .Where(u => u.AlertEmails && u.Email != null)
            .ToDictionaryAsync(u => u.UserName!, u => u.Email!, ct);
        if (emailByUser.Count == 0) return;

        var userNames = emailByUser.Keys.ToList();
        var alerts = await store.CardAlerts
            .Where(a => userNames.Contains(a.UserName)).ToListAsync(ct);
        if (alerts.Count == 0) return;

        var evaluated = await evaluator.EvaluateAsync(alerts);
        var clientUrl = config["ClientUrl"] ?? "https://cardstock.guide";
        var changed = false;

        foreach (var byUser in evaluated.GroupBy(e => e.Alert.UserName))
        {
            // Un-hit alerts re-arm quietly.
            foreach (var e in byUser.Where(e => !e.Hit && e.Alert.NotifiedAt != null))
            {
                e.Alert.NotifiedAt = null;
                changed = true;
            }

            var newlyHit = byUser.Where(e => e.Hit && e.Alert.NotifiedAt == null).ToList();
            if (newlyHit.Count == 0) continue;

            var lines = new List<string>();
            foreach (var e in newlyHit)
            {
                var card = await sources.Find(e.Alert.Game, e.Alert.ProductId);
                var name = card?.Name ?? $"{e.Alert.Game} #{e.Alert.ProductId}";
                lines.Add(
                    $"• {name}: {Describe(e.Alert)} (now {FormatValue(e.Alert, e.Current)})\n" +
                    $"  {clientUrl}/catalog/{e.Alert.Game}/{e.Alert.ProductId}");
            }

            var subject = newlyHit.Count == 1
                ? "CardStock alert hit"
                : $"CardStock: {newlyHit.Count} alerts hit";
            var body =
                "One of your CardStock price alerts just hit:\n\n" +
                string.Join("\n\n", lines) + "\n\n" +
                "Manage alerts from your watchlist: " + clientUrl + "/watchlist\n" +
                "To stop these emails, turn off alert emails in your profile settings.";

            if (await email.SendAsync(emailByUser[byUser.Key], subject, body))
            {
                foreach (var e in newlyHit) e.Alert.NotifiedAt = DateTime.UtcNow;
                changed = true;
                logger.LogInformation("Alert email sent to {User}: {Count} hit(s)", byUser.Key, newlyHit.Count);
            }
        }

        if (changed) await store.SaveChangesAsync(ct);
    }

    private static readonly Dictionary<string, string> TierLabel = new()
    {
        ["grade7"] = "Grade 7", ["grade8"] = "Grade 8", ["grade9"] = "Grade 9",
        ["grade95"] = "Grade 9.5", ["psa10"] = "PSA 10", ["bgs10"] = "BGS 10",
        ["cgc10"] = "CGC 10", ["sgc10"] = "SGC 10",
    };
    private static readonly Dictionary<string, string> Hz = new()
    {
        ["1w"] = "1W", ["1m"] = "1M", ["6m"] = "6M", ["12m"] = "1Y",
    };

    private static string Describe(CardAlert a)
    {
        var tier = a.Grade != null && TierLabel.TryGetValue(a.Grade, out var t) ? t : "Ungraded";
        var dir = a.Direction == "above" ? "at or above" : "at or below";
        var target = a.Kind == AlertKind.ForecastPct
            ? $"{(a.Target >= 0 ? "+" : "")}{a.Target:0.#}%" : a.Target.ToString("C2");
        return a.Kind switch
        {
            AlertKind.Price => $"{tier} price {dir} {target}",
            AlertKind.ForecastPrice => $"{tier} {Hz.GetValueOrDefault(a.Horizon ?? "", a.Horizon ?? "")} forecast {dir} {target}",
            _ => $"{tier} {Hz.GetValueOrDefault(a.Horizon ?? "", a.Horizon ?? "")} forecast growth {dir} {target}",
        };
    }

    private static string FormatValue(CardAlert a, double? v) =>
        v == null ? "no data"
        : a.Kind == AlertKind.ForecastPct ? $"{(v >= 0 ? "+" : "")}{v:0.#}%" : v.Value.ToString("C2");
}
