namespace API.RequestHelpers;

public class CardParams : PaginationParams
{
    public string? Game { get; set; }      // "onepiece" (default) or "pokemon"
    public string? OrderBy { get; set; }
    public string? SearchTerm { get; set; }
    public string? Sets { get; set; }      // comma separated
    public string? Rarities { get; set; }  // comma separated
    public string? Grade { get; set; }     // tier to price by: ungraded|lp|mp|grade7..psa10 (default: market)
}
