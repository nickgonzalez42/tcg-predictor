namespace API.Entities;

// One row of the model's public scorecard: how the forecasts for a (game,
// tier, horizon) have graded so far. Written by forecast_scorecard.py; read-only.
public class ForecastAccuracy
{
    public string Game { get; set; } = "";
    public string Target { get; set; } = "";
    public string Horizon { get; set; } = "";
    public long N { get; set; }                 // forecasts graded
    public double RetMae { get; set; }          // mean absolute log-return error
    public double RetBias { get; set; }         // mean (predicted - realized): + = runs hot
    public double BandHitRate { get; set; }     // share landing inside the 80% band
    public string? GradedThrough { get; set; }  // newest graded as-of date
}
