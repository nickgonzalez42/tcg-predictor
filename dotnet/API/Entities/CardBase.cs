namespace API.Entities;

// Columns shared by both the One Piece and Pokémon `cards` tables.
public abstract class CardBase
{
    public int Id { get; set; }              // product_id
    public string? Name { get; set; }
    public string? SetName { get; set; }     // set_name
    public string? Rarity { get; set; }
    public string? CardNumber { get; set; }  // card_number
    public string? CardType { get; set; }    // card_type
    public string? Description { get; set; }
    public double? MarketPrice { get; set; } // market_price (USD)
    public string? ImageUrl { get; set; }    // image_url (remote TCGplayer image)
}
