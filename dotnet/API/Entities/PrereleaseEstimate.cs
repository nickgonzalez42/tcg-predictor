namespace API.Entities;

// A trait-only launch-price estimate for a card that has NO market price yet
// (an upcoming set's presale listing, or a just-released card whose first
// sales haven't posted). Written by pipeline/forecast_prerelease.py into
// predictions.db (prerelease_estimates); read-only here. The regular model
// never sees these cards — it forecasts a return from an anchor price, and
// these have none — so a pre-release estimate is always labeled as such.
public class PrereleaseEstimate
{
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
    public string Printing { get; set; } = "";   // '' = base printing (the only one issued)
    public string? ReleaseDate { get; set; }     // the set's release date (ISO)
    public string AsOf { get; set; } = "";       // issue date
    public double Predicted { get; set; }        // expected price over the first ~2 months of trading
    public double? Low { get; set; }             // 80% range from held-out launches
    public double? High { get; set; }
    public string? Confidence { get; set; }      // low | med — from the holdout validation
    public string? Reason { get; set; }          // plain-English basis incl. the method's typical miss
    public string? ModelVersion { get; set; }
    public int? NTrain { get; set; }             // labeled launches the game's model learned from
    public int? ValSets { get; set; }            // held-out sets behind the quoted miss
    public double? ValMissPct { get; set; }      // typical (median) miss on those sets, in %
    public double? ValWithin50 { get; set; }     // share of held-out cards within ±50%
    public string? ScoredAt { get; set; }
}
