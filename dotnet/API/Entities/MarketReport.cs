namespace API.Entities;

// A weekly market report written by the pipeline (market_report.py) into
// predictions.db every Friday. Read-only here; served at /api/market-reports.
public class MarketReport
{
    public string Slug { get; set; } = "";
    public string Title { get; set; } = "";
    public string PublishedAt { get; set; } = "";   // yyyy-MM-dd
    public string Summary { get; set; } = "";
    public string BodyHtml { get; set; } = "";
    // Comma-separated display labels for the tag line on the reports index,
    // e.g. "Yu-Gi-Oh!" (2026-10-29). Empty for a report with no single
    // subject game, such as the model report.
    public string Tags { get; set; } = "";
}
