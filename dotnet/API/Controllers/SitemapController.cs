using System.Text;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Sitemaps, served at the site root (Caddy proxies /sitemap.xml and
// /sitemap-*.xml here; the SPA's static files handle everything else).
// One file per game lists every catalog-visible card page, so search engines
// see exactly the set of pages the catalog actually serves — regenerated from
// the card DBs on every request, nothing to sync.
[ApiController]
public class SitemapController(CardSources sources, IConfiguration config) : ControllerBase
{
    private const string XmlNs = "http://www.sitemaps.org/schemas/sitemap/0.9";

    private static readonly string[] StaticPaths =
        ["/", "/catalog", "/about", "/privacy", "/terms", "/contact"];

    private string Origin => (config["ClientUrl"] ?? "https://cardstock.guide").TrimEnd('/');

    [HttpGet("/sitemap.xml")]
    public async Task<IActionResult> Index()
    {
        var sb = new StringBuilder();
        sb.Append($"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<sitemapindex xmlns=\"{XmlNs}\">\n");
        sb.Append($"  <sitemap><loc>{Origin}/sitemap-static.xml</loc></sitemap>\n");
        foreach (var game in GameRegistry.Keys)
            if (await sources.Cards(game).VisibleInCatalog().AnyAsync())
                sb.Append($"  <sitemap><loc>{Origin}/sitemap-{game}.xml</loc></sitemap>\n");
        sb.Append("</sitemapindex>\n");
        return Xml(sb);
    }

    [HttpGet("/sitemap-static.xml")]
    public IActionResult StaticPages()
    {
        var sb = OpenUrlset();
        foreach (var path in StaticPaths)
            sb.Append($"  <url><loc>{Origin}{path}</loc></url>\n");
        return Xml(CloseUrlset(sb));
    }

    // Every catalog-visible card page for one game (all games fit the 50k-URL
    // sitemap limit; the biggest, yugioh, is ~31k).
    [HttpGet("/sitemap-{game}.xml")]
    public async Task<IActionResult> Game(string game)
    {
        var key = GameRegistry.Normalize(game);
        if (key == null) return NotFound();

        var ids = await sources.Cards(key).VisibleInCatalog()
            .Select(c => c.Id).OrderBy(id => id).ToListAsync();
        if (ids.Count == 0) return NotFound();

        var sb = OpenUrlset();
        foreach (var id in ids)
            sb.Append($"  <url><loc>{Origin}/catalog/{key}/{id}</loc></url>\n");
        return Xml(CloseUrlset(sb));
    }

    private static StringBuilder OpenUrlset() =>
        new StringBuilder().Append($"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"{XmlNs}\">\n");

    private static StringBuilder CloseUrlset(StringBuilder sb) => sb.Append("</urlset>\n");

    private ContentResult Xml(StringBuilder sb) =>
        Content(sb.ToString(), "application/xml; charset=utf-8");
}
