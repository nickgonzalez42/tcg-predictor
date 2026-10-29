using API.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Weekly market reports, written by the pipeline every Friday. "market-reports"
// rather than [controller]: /api/reports is the problem-report intake.
[Route("api/market-reports")]
[ApiController]
public class MarketReportsController(PredictionsContext predictions) : ControllerBase
{
    // Newest first; body omitted (the list page shows title + summary only).
    [HttpGet]
    public async Task<IActionResult> List()
    {
        try
        {
            var reports = await predictions.Reports
                .OrderByDescending(r => r.PublishedAt)
                .Select(r => new { r.Slug, r.Title, r.PublishedAt, r.Summary })
                .ToListAsync();
            return Ok(reports);
        }
        catch (SqliteException)
        {
            // predictions.db predates the reports table until the first Friday
            // run writes it — an empty list, not an error.
            return Ok(Array.Empty<object>());
        }
    }

    [HttpGet("{slug}")]
    public async Task<IActionResult> Get(string slug)
    {
        try
        {
            var report = await predictions.Reports.FirstOrDefaultAsync(r => r.Slug == slug);
            return report == null ? NotFound() : Ok(report);
        }
        catch (SqliteException)
        {
            return NotFound();
        }
    }
}
