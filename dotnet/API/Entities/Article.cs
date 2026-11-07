namespace API.Entities;

// A per-game article written by the pipeline (game_articles.py) into
// predictions.db: two a week per game, one on its newest set and one on the
// whole game (2026-11-06). Read-only here; served at /api/articles.
public class Article
{
    public string Slug { get; set; } = "";
    public string Kind { get; set; } = "";        // "set" | "game"
    public string Game { get; set; } = "";        // game key (pokemon, onepiece, ...)
    public string? SetName { get; set; }          // the set a "set" article covers
    public string Title { get; set; } = "";
    public string PublishedAt { get; set; } = ""; // yyyy-MM-dd
    public string Summary { get; set; } = "";
    public string BodyHtml { get; set; } = "";
    // The article's picture as inline SVG (report_thumbnail.render_svg): the
    // hero on the page and the thumbnail on the index.
    public string Hero { get; set; } = "";
}
