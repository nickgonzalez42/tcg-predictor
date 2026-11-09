namespace API.Entities;

// One week of TCGplayer sales for a card's variant and condition, recorded by
// the daytime sales crawl (pipeline/backfill_sales_volume.py -> the
// tcg_sales_history table in pricecharting.db, 2026-10-28). Buckets start on
// the day TCGplayer's own chart uses (Mondays); the newest bucket is the week
// that was still in progress when the crawl looked (see SalesScan.FetchedAt).
public class SalesBucket
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Variant { get; set; } = "";     // TCGplayer printing name: Foil, 1st Edition Holofoil, ...
    public string Condition { get; set; } = "";   // Near Mint, Lightly Played, ...
    public string BucketDate { get; set; } = "";  // YYYY-MM-DD, the bucket's first day
    public double? QuantitySold { get; set; }     // copies sold that week
    public double? TransactionCount { get; set; } // orders those copies came from
    public double? LowSale { get; set; }
    public double? HighSale { get; set; }
    public double? MarketPrice { get; set; }
}

// When the crawl last fetched a card's sales (tcg_sales_scan).
public class SalesScan
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string? FetchedAt { get; set; }        // ISO timestamp, UTC
}
