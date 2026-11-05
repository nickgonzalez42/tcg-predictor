namespace API.Entities;

// One landing week of the EARLY estimates (horizon launch1m: cards too new
// for the standard model), graded like the nightly 1-month cohorts. Written
// by forecast_scorecard.write_weekly_accuracy into
// forecast_accuracy_weekly_early (2026-11-05); read-only. Same shape as
// ForecastAccuracyWeekly, kept as its own class because EF maps one keyless
// entity to one table.
public class ForecastAccuracyWeeklyEarly
{
    public string Week { get; set; } = "";
    public long N { get; set; }
    public double? BandHitRate { get; set; }
    public double? RetMae { get; set; }
    public double? FlatMae { get; set; }
    public long? DirN { get; set; }
    public double? DirHitRate { get; set; }
    public long? N10 { get; set; }
    public double? BandHitRate10 { get; set; }
    public double? RetMae10 { get; set; }
    public double? FlatMae10 { get; set; }
}
