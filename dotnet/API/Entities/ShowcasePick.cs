namespace API.Entities;

// One home-page pick, decided nightly by pipeline/pick_showcase.py into
// predictions.db (table showcase, 2026-11-09): kind 'featured' = the biggest
// PAST price moves (slot = the window 1m | 6m | 1y, a gainer and a loser per
// game), kind 'expected' = the biggest FORECAST moves (slot 'game' = each
// game's strongest 1-month gainer and loser, 'global' = the ranking behind
// them). Every pick had at least a few Near Mint copies sold recently
// (`Sold`), checked with a fresh TCGplayer fetch. Read-only here.
public class ShowcasePick
{
    public string Kind { get; set; } = "";
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Printing { get; set; } = "";
    public string Slot { get; set; } = "";
    public string? Side { get; set; }
    public string? Horizon { get; set; }     // forecast horizon shown with the card (1m | 6m | 12m | launch1m)
    public int Rank { get; set; }
    public double? Pct { get; set; }         // the move that earned the pick, in percent
    public double? Price { get; set; }
    public int? Sold { get; set; }
    public string DecidedAt { get; set; } = "";
}
