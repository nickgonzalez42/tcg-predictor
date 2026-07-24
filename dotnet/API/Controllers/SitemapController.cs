using System.Text;
using API.Data;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;

namespace API.Controllers;

// Sitemaps, served at the site root (Caddy proxies /sitemap.xml and
// /sitemap-*.xml here; the SPA's static files handle everything else).
// One file per game lists every catalog-visible card page, so search engines
// see exactly the set of pages the catalog actually serves — regenerated from
// the card DBs, nothing to sync. Each sitemap's XML is multi-MB to build but
// changes at most daily (the pipeline refreshes the DBs overnight), so it's
// served from a 24h in-memory cache keyed per route; the rate limit covers
// cache-miss rebuild cost.
[ApiController]
[EnableRateLimiting("sitemap")]
public class SitemapController(
    CardSources sources, PredictionsContext predictions, IConfiguration config,
    IMemoryCache cache) : ControllerBase
{
    private const string XmlNs = "http://www.sitemaps.org/schemas/sitemap/0.9";

    private static readonly string[] StaticPaths =
        ["/", "/catalog", "/reports", "/about", "/privacy", "/terms", "/contact"];

    private string Origin => (config["ClientUrl"] ?? "https://cardstock.guide").TrimEnd('/');

    [HttpGet("/sitemap.xml")]
    public Task<IActionResult> Index() => Cached("sitemap:index", async () =>
    {
        var sb = new StringBuilder();
        sb.Append($"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<sitemapindex xmlns=\"{XmlNs}\">\n");
        sb.Append($"  <sitemap><loc>{Origin}/sitemap-static.xml</loc></sitemap>\n");
        if ((await ReportSlugs()).Count > 0)
            sb.Append($"  <sitemap><loc>{Origin}/sitemap-reports.xml</loc></sitemap>\n");
        foreach (var game in GameRegistry.Keys)
            if (await sources.Cards(game).VisibleInCatalog().AnyAsync())
                sb.Append($"  <sitemap><loc>{Origin}/sitemap-{game}.xml</loc></sitemap>\n");
        sb.Append("</sitemapindex>\n");
        return sb.ToString();
    });

    [HttpGet("/sitemap-static.xml")]
    public Task<IActionResult> StaticPages() => Cached("sitemap:static", () =>
    {
        var sb = OpenUrlset();
        foreach (var path in StaticPaths)
            sb.Append($"  <url><loc>{Origin}{path}</loc></url>\n");
        return Task.FromResult<string?>(CloseUrlset(sb).ToString());
    });

    // Every catalog-visible card page for one game (all games fit the 50k-URL
    // sitemap limit; the biggest, yugioh, is ~31k).
    [HttpGet("/sitemap-{game}.xml")]
    public async Task<IActionResult> Game(string game)
    {
        // Normalize BEFORE touching the cache so arbitrary {game} values can't
        // mint cache keys — only the canonical game ids ever get an entry.
        var key = GameRegistry.Normalize(game);
        if (key == null) return NotFound();

        return await Cached($"sitemap:{key}", async () =>
        {
            var ids = await sources.Cards(key).VisibleInCatalog()
                .Select(c => c.Id).OrderBy(id => id).ToListAsync();
            if (ids.Count == 0) return null;

            var sb = OpenUrlset();
            foreach (var id in ids)
                sb.Append($"  <url><loc>{Origin}/catalog/{key}/{id}</loc></url>\n");
            return CloseUrlset(sb).ToString();
        });
    }

    [HttpGet("/sitemap-reports.xml")]
    public Task<IActionResult> Reports() => Cached("sitemap:reports", async () =>
    {
        var slugs = await ReportSlugs();
        if (slugs.Count == 0) return null;

        var sb = OpenUrlset();
        foreach (var slug in slugs)
            sb.Append($"  <url><loc>{Origin}/reports/{slug}</loc></url>\n");
        return CloseUrlset(sb).ToString();
    });

    // Serve one sitemap from the cache, building it on a miss. A null build
    // means "no such sitemap right now" (no cards, no reports yet) — answered
    // 404 and NOT cached, so the sitemap appears as soon as the data does.
    private async Task<IActionResult> Cached(string key, Func<Task<string?>> build)
    {
        if (!cache.TryGetValue(key, out string? xml))
        {
            xml = await build();
            if (xml == null) return NotFound();
            cache.Set(key, xml, TimeSpan.FromHours(24));
        }
        return Content(xml!, "application/xml; charset=utf-8");
    }

    // Empty until the pipeline's first Friday run creates the reports table.
    private async Task<List<string>> ReportSlugs()
    {
        try { return await predictions.Reports.Select(r => r.Slug).ToListAsync(); }
        catch (SqliteException) { return []; }
    }

    private static StringBuilder OpenUrlset() =>
        new StringBuilder().Append($"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"{XmlNs}\">\n");

    private static StringBuilder CloseUrlset(StringBuilder sb) => sb.Append("</urlset>\n");
}
