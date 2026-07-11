namespace API.Entities;

// Columns shared by every game's `cards` table.
public abstract class CardBase
{
    public int Id { get; set; }              // product_id
    public string? Name { get; set; }
    public string? SetName { get; set; }     // set_name
    public string? Rarity { get; set; }
    public string? CardNumber { get; set; }  // card_number
    public string? CardType { get; set; }    // card_type
    public string? Description { get; set; }
    public double? NearMintPrice { get; set; } // near_mint_price — latest ungraded price (PriceCharting) from price_history_unified
    public string? ImageUrl { get; set; }    // image_url (remote TCGplayer image)
    public string? ImagePath { get; set; }   // image_path — local art on disk; NULL = art pending,
                                             // and the card is stored but served nowhere on the site
}

// Card from any game added after the original two (yugioh, magic, lorcana,
// digimon, gundam, ...): the generic scraper stores the shared columns plus
// the game's whole stat line as a JSON blob, so no per-game schema is needed.
public class GenericCard : CardBase
{
    public string? CustomAttributes { get; set; }   // custom_attributes JSON
}
