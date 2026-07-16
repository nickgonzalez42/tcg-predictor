namespace API.Entities;

// Current graded/ungraded prices from PriceCharting, keyed game + card id.
// Imported offline; read-only as far as the app is concerned. Prices in USD.
public class GradedPrice
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
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
    public string? UpdatedAt { get; set; }

    // The tier's current price (null = no data at that tier). Blank or
    // unrecognized tiers read the ungraded column.
    public double? PriceFor(string? tier) => tier switch
    {
        "grade7" => Grade7,
        "grade8" => Grade8,
        "grade9" => Grade9,
        "grade95" => Grade95,
        "psa10" => Psa10,
        "bgs10" => Bgs10,
        "cgc10" => Cgc10,
        "sgc10" => Sgc10,
        _ => Ungraded,
    };
}
