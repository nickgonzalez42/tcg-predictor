namespace API.RequestHelpers;

public class CardParams : PaginationParams
{
    public string? Game { get; set; }      // "onepiece" (default) or "pokemon"
    public string? OrderBy { get; set; }
    public string? SearchTerm { get; set; }
    public string? Sets { get; set; }      // comma separated
    public string? Rarities { get; set; }  // comma separated
    public string? Grade { get; set; }     // tier to price by: ungraded|lp|mp|grade7..psa10 (default: market)
    public string? Trend { get; set; }     // trend window for sparkline/movement: 1w|1m|6m|1y (default 1m)
    public double? MinPrice { get; set; }  // range filter on the SHOWN price (the selected tier's)
    public double? MaxPrice { get; set; }
}
