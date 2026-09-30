namespace API.Entities;

// Raw PriceCharting price rows (graded_price_history) — kept as-scraped and
// non-destructive. The chart uses the UNGRADED rows to bridge the source
// cutover (2026-08-15): unify drops PC ungraded from July 2026 in favor of
// TCGplayer, but cards whose NM crawl started late in the cutover have a
// multi-week hole PC actually priced (often daily, via the subscription's
// final weeks). Served read-only, merged behind unified + NM points.
public class PcRawPoint
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Printing { get; set; } = ""; // '' = base printing
    public string Grade { get; set; } = "";
    public string Date { get; set; } = "";     // YYYY-MM-DD
    public double Price { get; set; }
}
