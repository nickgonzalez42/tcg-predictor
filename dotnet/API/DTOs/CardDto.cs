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
    // Forecast metric, populated only when sorting by expected change so the card
    // can show the change instead of the price.
    public double? ExpectedChange { get; set; }   // value of the sorted metric (% or USD delta)
    public string? ExpectedUnit { get; set; }      // "percent" | "usd"
    public string? ExpectedHorizon { get; set; }   // "6m" | "12m"
    public double? ExpectedFrom { get; set; }      // current (forecast base) price
    public double? ExpectedTo { get; set; }        // forecast price
    // Lightweight market context for tiles / screener rows, computed for the shown
    // condition tier over the requested trend window (1w|1m|6m|1y).
    public string? PriceAsOf { get; set; }        // date of the shown price's latest history point
    public List<double>? Sparkline { get; set; }  // prices inside the trend window, oldest first
    public int? HistoryMonths { get; set; }        // months of history, full series (confidence proxy)
    public double? TrendPct { get; set; }          // % change across the window
    public string? TrendPeriod { get; set; }       // normalized window this was computed for
    public double? Fcst6Pct { get; set; }          // 6m forecast % change
    public double? Fcst12Pct { get; set; }         // 12m forecast % change
    public double? Fcst12To { get; set; }          // 12m forecast price
    // Forecast matched to the requested trend window (1w->1w, 1m->1m, 6m->6m, 1y->12m).
    public double? FcstTo { get; set; }
    public string? FcstHorizon { get; set; }       // which horizon FcstPct/FcstTo describe
    public string? FcstConfidence { get; set; }    // model-reported: high | med | low
    // Wishlist rows only.
    public double? WatchedAtPrice { get; set; }    // price when the card was wishlisted
    public DateTime? WatchedSince { get; set; }    // when the card was wishlisted
    public double? AlertTargetPrice { get; set; }  // user's "notify at or below" price
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
    public string? UpdatedAt { get; set; }   // when the PriceCharting snapshot was taken
}
