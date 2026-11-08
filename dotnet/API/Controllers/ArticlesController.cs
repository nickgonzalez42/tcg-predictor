using API.Data;
using API.RequestHelpers;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Per-game articles (2026-11-06): a set report and a game report per game,
// written by the pipeline's game_articles.py into predictions.db on its own
// schedule and served read-only.
[Route("api/articles")]
[ApiController]
public class ArticlesController(PredictionsContext predictions) : ControllerBase
{
    private const int MaxPage = 60;

    // Newest first; the body is omitted, the hero rides along for the index
    // cards. ?game= and ?kind= narrow the list; ?limit= / ?offset= page it.
    [HttpGet]
    public async Task<IActionResult> List(string? game, string? kind, int limit = 24, int offset = 0)
    {
        limit = Math.Clamp(limit, 1, MaxPage);
        offset = Math.Max(offset, 0);
        try
        {
            var q = predictions.Articles.AsQueryable();
            var key = string.IsNullOrEmpty(game) ? null : GameRegistry.Normalize(game);
            if (key != null) q = q.Where(a => a.Game == key);
            if (kind is "set" or "game") q = q.Where(a => a.Kind == kind);
            var total = await q.CountAsync();
            var items = await q
                .OrderByDescending(a => a.PublishedAt).ThenBy(a => a.Slug)
                .Skip(offset).Take(limit)
                .Select(a => new { a.Slug, a.Kind, a.Focus, a.Game, a.SetName, a.Title, a.PublishedAt, a.Summary, a.Hero })
                .ToListAsync();
            return Ok(new { total, items });
        }
        catch (SqliteException)
        {
            // predictions.db predates the articles table until the first run
            // writes it: an empty list, not an error.
            return Ok(new { total = 0, items = Array.Empty<object>() });
        }
    }

    [HttpGet("{slug}")]
    public async Task<IActionResult> Get(string slug)
    {
        try
        {
            var article = await predictions.Articles.FirstOrDefaultAsync(a => a.Slug == slug);
            return article == null ? NotFound() : Ok(article);
        }
        catch (SqliteException)
        {
            return NotFound();
        }
    }
}
