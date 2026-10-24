namespace API.DTOS;

// Mark owned copies as sold (POST /watchlist/sold): the copy ids (a stack's
// newest N, or one copy from the editor), the sale price per copy, and an
// optional sale date / note.
public class SellCopiesDto
{
    public required List<int> Ids { get; set; }
    public double Price { get; set; }
    public DateTime? SoldAt { get; set; }
    public string? Note { get; set; }
}

// Edit a sale (PATCH /watchlist/sold/{id}).
public class UpdateSoldDto
{
    public double Price { get; set; }
    public DateTime? SoldAt { get; set; }
    public string? Note { get; set; }
}

// One sold copy with its card context and realized result (GET /watchlist/sold).
public class SoldCopyDto
{
    public int Id { get; set; }
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string? Name { get; set; }
    public string? SetName { get; set; }
    public string? Rarity { get; set; }
    public string? PictureUrl { get; set; }
    public string? Grade { get; set; }
    public string Printing { get; set; } = "";
    public string Source { get; set; } = "pack";      // pack | paid
    public double PurchasePrice { get; set; }         // cost basis (0 for pack pulls)
    public DateTime AcquiredAt { get; set; }
    public double SoldPrice { get; set; }
    public DateTime SoldAt { get; set; }
    public string? Note { get; set; }
    public double PlUsd { get; set; }                 // sold − basis (basis 0 for pack pulls)
    public double? PlPct { get; set; }                // only when a cost basis exists
    public int HeldDays { get; set; }
}
