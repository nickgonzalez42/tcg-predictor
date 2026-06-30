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
    public double? PredictedPrice { get; set; }   // model-predicted price in USD
    public bool UsedImage { get; set; }           // prediction used the card art
    public string? PictureUrl { get; set; }       // local image served by the API
    public string? ImageUrl { get; set; }         // remote fallback image
    // Game-specific fields (One Piece color/power/…, Pokémon hp/attacks/…), null/empty omitted.
    public Dictionary<string, string> Attributes { get; set; } = [];
}
