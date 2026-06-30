namespace API.Entities;

// A model-predicted price for a card, keyed by game + card id.
// Produced offline by the ML pipeline; read-only as far as the app is concerned.
public class Prediction
{
    public string Game { get; set; } = "";   // "onepiece" | "pokemon"
    public int ProductId { get; set; }        // joins to a card's Id
    public double PredictedPrice { get; set; }
    public double? ActualPrice { get; set; }  // market price when scored (null = unpriced)
    public bool UsedImage { get; set; }       // true = tabular+image model, false = tabular fallback
    public string? ModelVersion { get; set; }
    public string? ScoredAt { get; set; }
}
