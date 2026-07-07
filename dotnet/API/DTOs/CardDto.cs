namespace API.DTOS;

public class CardDto
{
    public int Id { get; set; }
    public string? Name { get; set; }
    public string Game { get; set; } = "";
    public string? SetName { get; set; }
    public string? Rarity { get; set; }
    public string? CardNumber { get; set; }
    public string? CardType { get; set; }
    public string? Description { get; set; }
    public double? Price { get; set; }            // actual market price in USD
    public string? PictureUrl { get; set; }       // local image served by the API
    public string? ImageUrl { get; set; }         // remote fallback image
    // Game-specific fields (One Piece color/power/…, Pokémon hp/attacks/…), null/empty omitted.
    public Dictionary<string, string> Attributes { get; set; } = [];
    // PriceCharting graded/ungraded prices (detail view); null when unmatched.
    public GradedPriceDto? GradedPrices { get; set; }
    // The signed-in user's owned copies of this card; only populated by the
    // Owned tracked list, null elsewhere. On the Owned list each DTO is one
    // (card + condition) unit, so these are the copies at OwnedGrade only.
    public List<OwnedCopyDto>? OwnedCopies { get; set; }
    public string? OwnedGrade { get; set; }    // the condition this owned unit represents (copy-grade vocab)
    public int? OwnedQuantity { get; set; }     // number of copies at that condition
}

public class GradedPriceDto
{
    public double? Ungraded { get; set; }
    public double? Grade7 { get; set; }
    public double? Grade8 { get; set; }
    public double? Grade9 { get; set; }
    public double? Grade95 { get; set; }
    public double? Psa10 { get; set; }
    public double? Bgs10 { get; set; }
    public double? Cgc10 { get; set; }
    public double? Sgc10 { get; set; }
    public int? SalesVolume { get; set; }
}
