namespace API.RequestHelpers;

// Owned copy-grade vocabulary mapped onto the price-history and forecast tiers.
// Shared by every controller that prices or forecasts by condition.
public static class GradeTiers
{
    // Tiers that have their own forecasts; anything else falls back to ungraded.
    public static readonly HashSet<string> Graded =
        ["grade7", "grade8", "grade9", "grade95", "psa10", "bgs10", "cgc10", "sgc10"];

    // price_history_unified tier. Pricing is PriceCharting-only, which has no
    // played-condition series — NM/LP/MP/unspecified all read the ungraded
    // (raw/loose) series; graded tiers read their own.
    public static string PriceTier(string? grade) =>
        string.IsNullOrEmpty(grade) || grade is "nm" or "lp" or "mp" ? "ungraded" : grade;

    public static string ForecastTarget(string? grade) =>
        !string.IsNullOrEmpty(grade) && Graded.Contains(grade) ? grade : "ungraded";
}
