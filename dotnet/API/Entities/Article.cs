namespace API.Entities;

// A per-game article written by the pipeline (game_articles.py) into
// predictions.db (2026-11-06): a set report and a game report per game, each
// at least every two weeks on a random night. Read-only here; served at
// /api/articles.
public class Article
{
    public string Slug { get; set; } = "";
    public string Kind { get; set; } = "";        // "set" | "game"
    public string Game { get; set; } = "";        // game key (pokemon, onepiece, ...)
    public string? SetName { get; set; }          // the set a "set" article covers
    // 2026-11-07: a "set" article is about the newest set ("set") or the game's
    // other recent releases ("recent"); null on older rows and on game reports.
    public string? Focus { get; set; }
    public string Title { get; set; } = "";
    public string PublishedAt { get; set; } = ""; // yyyy-MM-dd
    public string Summary { get; set; } = "";
    public string BodyHtml { get; set; } = "";
    // The article's picture as inline SVG (report_thumbnail.render_svg): the
    // hero on the page and the thumbnail on the index.
    public string Hero { get; set; } = "";
}
