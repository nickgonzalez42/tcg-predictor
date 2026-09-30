namespace API.Entities;

// Dated Near-Mint prices straight from the nightly TCGplayer crawl — daily
// fleet-wide since 2026-07-28, weekly buckets further back for backfilled
// cards. The unified table compresses these to month buckets for storage;
// the chart merges them back in for a detailed recent line (2026-08-15).
public class NmDailyPoint
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Printing { get; set; } = ""; // '' = base printing
    public string Date { get; set; } = "";     // YYYY-MM-DD
    public double Price { get; set; }
}
