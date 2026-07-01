namespace API.Entities;

// One monthly price point for a card at a given condition tier.
// Read from the PriceCharting history now; will point at the unified
// (TCGplayer-blended) history table later — same shape.
public class PriceHistoryPoint
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Grade { get; set; } = "";   // ungraded, grade7..psa10, bgs10, cgc10, sgc10
    public string Date { get; set; } = "";     // YYYY-MM-DD
    public double Price { get; set; }
    public string? Source { get; set; }        // tcgplayer | pricecharting
}
