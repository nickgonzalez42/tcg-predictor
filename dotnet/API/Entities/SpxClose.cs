namespace API.Entities;

// One daily S&P 500 closing level, cached in store.db (topped up from Yahoo
// Finance by SpxService). Backs the portfolio's "same $ in the market"
// benchmark line. Date is yyyy-MM-dd, matching price_history_unified.
public class SpxClose
{
    public required string Date { get; set; }
    public double Close { get; set; }
}
