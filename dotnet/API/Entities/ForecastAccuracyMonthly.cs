namespace API.Entities;

// Full-cohort accuracy of one (game, tier, horizon) for one issue month,
// persisted once by the 1m compactor (forecast_scorecard.compact_1m).
public class ForecastAccuracyMonthly
{
    public string Game { get; set; } = "";
    public string Target { get; set; } = "";
    public string Horizon { get; set; } = "";
    public string Month { get; set; } = "";
    public long N { get; set; }
    public double? RetMae { get; set; }
    public double? RetBias { get; set; }
    public double? BandHitRate { get; set; }
    public string? GradedThrough { get; set; }
}
