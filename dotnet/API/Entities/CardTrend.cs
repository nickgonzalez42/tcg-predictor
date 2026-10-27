namespace API.Entities;

// One row per (game, grade, product_id): the card's price change over each
// trend window, computed nightly by pipeline/build_card_trends.py into
// predictions.db (table card_trends). The catalog's "What already happened"
// sorts and the tracked lists read this instead of re-deriving it from the
// full price history on every request (2026-10-26). Read-only here.
public class CardTrend
{
    public string Game { get; set; } = "";
    public string Grade { get; set; } = "";      // ungraded | grade7 | ... | psa10
    public int ProductId { get; set; }
    public string? LatestDate { get; set; }
    public double? LatestPrice { get; set; }
    public double? Pct1w { get; set; }
    public double? Usd1w { get; set; }
    public double? Pct1m { get; set; }
    public double? Usd1m { get; set; }
    public double? Pct6m { get; set; }
    public double? Usd6m { get; set; }
    public double? Pct1y { get; set; }
    public double? Usd1y { get; set; }
    public string? AsOf { get; set; }
}
