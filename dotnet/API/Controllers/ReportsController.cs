using API.Data;
using API.Entities;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace API.Controllers;

// "Report a problem" intake. Signed-in users only (the client hides the tab
// when signed out); stores the report in store.db and fires a best-effort
// notification (see NotificationService).
[Authorize]
public class ReportsController(StoreContext store, NotificationService notifier) : BaseApiController
{
    public record ReportDto(string Message, string? PageUrl, string? Email);

    // Lightweight per-IP flood guard (in-memory; single server). Not PII-stored.
    private static readonly Dictionary<string, List<DateTime>> Recent = new();
    private static readonly object Gate = new();
    private const int MaxPerWindow = 5;
    private static readonly TimeSpan Window = TimeSpan.FromMinutes(10);

    [HttpPost]
    public async Task<IActionResult> Submit(ReportDto dto)
    {
        var message = dto.Message?.Trim() ?? "";
        if (message.Length < 5)
            return BadRequest("Please describe the problem in a few words.");
        if (message.Length > 4000)
            message = message[..4000];

        var email = dto.Email?.Trim();
        if (!string.IsNullOrEmpty(email) && (email.Length > 200 || !email.Contains('@')))
            return BadRequest("That email address doesn't look right.");

        if (TooMany(ClientIp()))
            return BadRequest("Thanks — you've sent a few reports recently. Please try again later.");

        var report = new ProblemReport
        {
            Message = message,
            PageUrl = Clip(dto.PageUrl?.Trim(), 500),
            Email = email,
            UserName = User.Identity!.Name,
            UserAgent = Clip(Request.Headers.UserAgent.ToString(), 400),
        };
        store.ProblemReports.Add(report);
        await store.SaveChangesAsync();

        await notifier.NotifyProblemReport(report);   // best-effort; won't throw
        return Ok(new { ok = true });
    }

    // Behind Caddy, so prefer the forwarded client IP.
    private string ClientIp() =>
        Request.Headers.TryGetValue("X-Forwarded-For", out var f) && f.Count > 0
            ? f[0]!.Split(',')[0].Trim()
            : HttpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown";

    private static bool TooMany(string ip)
    {
        lock (Gate)
        {
            var floor = DateTime.UtcNow - Window;
            if (!Recent.TryGetValue(ip, out var times)) { times = []; Recent[ip] = times; }
            times.RemoveAll(t => t < floor);
            if (times.Count >= MaxPerWindow) return true;
            times.Add(DateTime.UtcNow);
            return false;
        }
    }

    private static string? Clip(string? s, int n) =>
        string.IsNullOrEmpty(s) ? s : s.Length <= n ? s : s[..n];
}
