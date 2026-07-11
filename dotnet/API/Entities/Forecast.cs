namespace API.Entities;

// A model price forecast for a card at a given target tier + horizon.
// Produced offline by the forecasting pipeline; read-only. Prices in USD.
public class Forecast
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Target { get; set; } = "";    // ungraded | psa10
    public string Horizon { get; set; } = "";    // 6m | 12m
    public string? AsOf { get; set; }
    public double BasePrice { get; set; }
    public double ForecastPrice { get; set; }
    public double Low { get; set; }              // confidence band (1 MAE)
    public double High { get; set; }
    public double Ret { get; set; }              // predicted log-return
    public string? Reason { get; set; }          // plain-English why, from model signals
    public string? Confidence { get; set; }      // model-reported: high | med | low (80% interval width)
    public string? ScoredAt { get; set; }        // batch timestamp — invalidates cached prose
}
