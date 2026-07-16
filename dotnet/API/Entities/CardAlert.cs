namespace API.Entities;

// A price/forecast alert on a card. A user can hold several per card, each
// scoped to a condition tier and (for forecast kinds) a horizon. Lives in
// store.db alongside users. Display-only for now: the client surfaces hit
// states; push/email delivery can hook in later.
public class CardAlert
{
    public int Id { get; set; }
    public required string UserName { get; set; }  // owner (Identity user name / email)
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public string? Grade { get; set; }             // condition tier; null = ungraded
    public string Kind { get; set; } = AlertKind.Price;
    public string? Horizon { get; set; }           // forecast kinds only: 1w | 1m | 6m | 12m
    public string Direction { get; set; } = "below";   // above | below (at-or-)
    public double Target { get; set; }             // $ for price kinds, % for fcst_pct
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    // Edge-trigger state for email delivery: stamped when a hit is emailed so
    // it isn't re-sent daily; cleared when the alert un-hits, re-arming it.
    public DateTime? NotifiedAt { get; set; }
}

public static class AlertKind
{
    public const string Price = "price";               // current market price
    public const string ForecastPrice = "fcst_price";  // model forecast price at a horizon
    public const string ForecastPct = "fcst_pct";      // model forecast % change at a horizon
}
