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
    // The report's picture as inline SVG: the article's hero and the index
    // card's thumbnail, with every card and game bar in it a link
    // (pipeline/report_thumbnail.py). Empty for reports written before it.
    public string Hero { get; set; } = "";
}
