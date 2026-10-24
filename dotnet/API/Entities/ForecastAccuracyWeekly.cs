namespace API.Entities;

// One landing week of the nightly 1-month Near Mint cohorts (full cohort:
// live rows + the tallies banked at compaction). Written by
// forecast_scorecard.write_weekly_accuracy; read-only. Keyed by the Friday
// the landing week starts.
public class ForecastAccuracyWeekly
{
    public string Week { get; set; } = "";
    public long N { get; set; }
    public double? BandHitRate { get; set; }
    public double? RetMae { get; set; }
    public double? FlatMae { get; set; }       // hold-flat baseline: mean |realized|
    public long? DirN { get; set; }
    public double? DirHitRate { get; set; }
    public long? N10 { get; set; }             // the same for cards priced $10+
    public double? BandHitRate10 { get; set; }
    public double? RetMae10 { get; set; }
    public double? FlatMae10 { get; set; }
}
