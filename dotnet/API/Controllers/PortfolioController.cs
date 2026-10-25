using API.Data;
using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Brokerage-style rollup of the signed-in user's owned copies: total value,
// a monthly value-over-time series, allocation, and best/worst positions.
// Prices come from price_history_unified per condition tier (monthly points),
// so "day" granularity doesn't exist — the change figures are month-over-month.
[Authorize]
public class PortfolioController(
    StoreContext store, PriceChartingContext priceCharting, CardSources sources,
    PredictionsContext predictions, SpxService spx) : BaseApiController
{
    private static readonly Dictionary<string, string> TierLabels = new()
    {
        ["ungraded"] = "Ungraded", ["grade7"] = "Grade 7", ["grade8"] = "Grade 8",
        ["grade9"] = "Grade 9", ["grade95"] = "Grade 9.5", ["psa10"] = "PSA 10",
        ["bgs10"] = "BGS 10", ["cgc10"] = "CGC 10", ["sgc10"] = "SGC 10",
    };

    private static string TierLabel(string tier) => TierLabels.GetValueOrDefault(tier, tier);

    // Optional ?game= narrows the whole rollup to one game's copies — the
    // value chart's game chips use this; the page's headline numbers keep
    // using the unfiltered call.
    // includePacks=false drops pack pulls from the whole rollup (value,
    // chart, allocation, count) — the "hide cards I didn't buy" view. Pack
    // pulls NEVER enter the money sections (paid P/L, invested, benchmark)
    // either way: no dollars were individually spent on them.
    // Grading analysis (2026-10-25): the user's Near Mint (ungraded) owned
    // cards with the latest price at every PSA tier, so the client can rank
    // what grading would add and sort by any grade. One row per distinct
    // (card, printing); copies counted. NM = the same unified series the
    // portfolio values ungraded copies with (card's NM column as fallback).
    [HttpGet("grading")]
    public async Task<IActionResult> GetGradingCandidates()
    {
        var user = User.Identity!.Name!;
        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Owned
                        && (x.Grade == null || x.Grade == ""))
            .ToListAsync();
        var tiers = new[] { "ungraded", "grade7", "grade8", "grade9", "grade95", "psa10" };
        var rows = new List<object>();
        foreach (var g in copies.GroupBy(c => c.Game))
        {
            var ids = g.Select(c => c.ProductId).Distinct().ToList();
            var hist = await priceCharting.History
                .Where(h => h.Game == g.Key && ids.Contains(h.ProductId) && tiers.Contains(h.Grade))
                .Select(h => new { h.ProductId, h.Printing, h.Grade, h.Date, h.Price })
                .ToListAsync();
            var latest = hist
                .GroupBy(h => (h.ProductId, h.Printing, h.Grade))
                .ToDictionary(x => x.Key, x => x.OrderByDescending(h => h.Date).First());
            var cards = (await sources.Cards(g.Key).Where(c => ids.Contains(c.Id)).ToListAsync())
                .ToDictionary(c => c.Id);
            foreach (var unit in g.GroupBy(c => (c.ProductId, Printing: c.Printing ?? "")))
            {
                var (pid, printing) = unit.Key;
                if (!cards.TryGetValue(pid, out var card)) continue;
                double? At(string tier) =>
                    latest.TryGetValue((pid, printing, tier), out var p) && p.Price > 0 ? p.Price : null;
                var nm = At("ungraded") ?? (printing == "" ? card.NearMintPrice : null);
                if (nm is not > 0) continue;
                var graded = new Dictionary<string, double?>
                {
                    ["grade7"] = At("grade7"), ["grade8"] = At("grade8"), ["grade9"] = At("grade9"),
                    ["grade95"] = At("grade95"), ["psa10"] = At("psa10"),
                };
                if (graded.Values.All(v => v == null)) continue;   // no graded market: nothing to say
                var asOf = latest.Where(kv => kv.Key.ProductId == pid && kv.Key.Printing == printing
                                              && kv.Key.Grade != "ungraded")
                    .Select(kv => kv.Value.Date).DefaultIfEmpty("").Max();
                rows.Add(new
                {
                    game = g.Key,
                    productId = pid,
                    printing,
                    name = card.Name,
                    setName = card.SetName,
                    cardNumber = card.CardNumber,
                    pictureUrl = CardImageUrl(g.Key, pid),
                    copies = unit.Count(),
                    nm = Math.Round(nm.Value, 2),
                    grades = graded,
                    asOf,
                });
            }
        }
        return Ok(new { nmCopies = copies.Count, cards = rows });
    }

    // Collection analysis (2026-10-25): one pass over the user's owned copies
    // into the figures the Tools > Collection analysis modal draws — where the
    // value sits (game, condition, set, rarity, price band, concentration),
    // how it got there (acquisitions by month, holding time), what it did last
    // month, what the model expects, and the cards that matter most.
    [HttpGet("analysis")]
    public async Task<IActionResult> GetAnalysis()
    {
        var user = User.Identity!.Name!;
        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Owned)
            .ToListAsync();
        if (copies.Count == 0) return Ok(new { copies = 0 });

        // Latest price per (game, product, tier) and the price ~30 days back.
        var seriesByKey = new Dictionary<(string Game, int Id, string Tier), List<(string Date, double Price)>>();
        var meta = new Dictionary<(string Game, int Id), CardBase>();
        foreach (var gameGroup in copies.GroupBy(c => c.Game))
        {
            var ids = gameGroup.Select(c => c.ProductId).Distinct().ToList();
            var tiers = gameGroup.Select(c => GradeTiers.PriceTier(c.Grade)).Distinct().ToList();
            var rows = await priceCharting.History
                .Where(h => h.Game == gameGroup.Key && ids.Contains(h.ProductId) && tiers.Contains(h.Grade))
                .Select(h => new { h.ProductId, h.Grade, h.Date, h.Price })
                .ToListAsync();
            foreach (var g in rows.GroupBy(r => (r.ProductId, r.Grade)))
                seriesByKey[(gameGroup.Key, g.Key.ProductId, g.Key.Grade)] =
                    g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList();
            foreach (var card in await sources.Cards(gameGroup.Key).Where(c => ids.Contains(c.Id)).ToListAsync())
                meta[(gameGroup.Key, card.Id)] = card;
        }
        List<(string Date, double Price)>? SeriesOf(TrackedCard c) =>
            seriesByKey.GetValueOrDefault((c.Game, c.ProductId, GradeTiers.PriceTier(c.Grade)));
        double ValueOf(TrackedCard c) => SeriesOf(c) is { Count: > 0 } s ? s[^1].Price : 0;
        var monthAgo = DateTime.UtcNow.AddDays(-30).ToString("yyyy-MM-dd");
        double? AgoOf(TrackedCard c)
        {
            if (SeriesOf(c) is not { Count: > 0 } s) return null;
            var p = s.LastOrDefault(x => string.CompareOrdinal(x.Date, monthAgo) <= 0);
            return p.Price > 0 ? p.Price : null;
        }
        string OwnedDate(TrackedCard c) => (c.AcquiredAt ?? c.AddedAt).ToString("yyyy-MM-dd");
        CardBase? MetaOf(TrackedCard c) => meta.GetValueOrDefault((c.Game, c.ProductId));

        var total = copies.Sum(ValueOf);
        double Share(double v) => total > 0 ? Math.Round(v / total * 100, 1) : 0;

        // ----- headline -----
        var paidCopies = copies.Where(c => c.Source == AcquireSource.Paid && c.PurchasePrice is > 0).ToList();
        var invested = paidCopies.Sum(c => c.PurchasePrice!.Value);
        var paidValue = paidCopies.Sum(ValueOf);
        var sold = await store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Sold).ToListAsync();
        var realizedPl = sold.Sum(c => c.SoldPrice ?? 0)
                         - sold.Where(c => c.Source == AcquireSource.Paid).Sum(c => c.PurchasePrice ?? 0);
        var cardValues = copies.GroupBy(c => (c.Game, c.ProductId))
            .Select(g => new { g.Key.Game, Id = g.Key.ProductId, Value = g.Sum(ValueOf), Copies = g.Count() })
            .OrderByDescending(x => x.Value).ToList();
        var priced = cardValues.Where(x => x.Value > 0).Select(x => x.Value).OrderBy(v => v).ToList();
        var median = priced.Count == 0 ? 0
            : priced.Count % 2 == 1 ? priced[priced.Count / 2]
            : (priced[priced.Count / 2 - 1] + priced[priced.Count / 2]) / 2;

        // ----- breakdowns -----
        List<object> Breakdown(Func<TrackedCard, string> key, int keep = 8) => copies
            .GroupBy(key)
            .Select(g => new { label = g.Key, value = g.Sum(ValueOf), copies = g.Count() })
            .Where(a => a.value > 0)   // unpriced cards would only add empty slices
            .OrderByDescending(a => a.value)
            .Select((a, i) => (a, i))
            .GroupBy(t => t.i < keep ? t.a.label : "Other")
            .Select(g => new { label = g.Key, value = g.Sum(t => t.a.value), copies = g.Sum(t => t.a.copies), other = g.Key == "Other" })
            .OrderBy(a => a.other).ThenByDescending(a => a.value)
            .Select(a => (object)new { a.label, value = Math.Round(a.value, 2), a.copies, pct = Share(a.value) })
            .ToList();
        var byGame = Breakdown(c => GameRegistry.Label(c.Game), 12);
        var byTier = Breakdown(c => TierLabel(GradeTiers.PriceTier(c.Grade)), 12);
        var bySet = Breakdown(c => MetaOf(c)?.SetName is { Length: > 0 } sn ? sn : "Unknown set");
        var byRarity = Breakdown(c => MetaOf(c)?.Rarity is { Length: > 0 } r ? r : "Unknown rarity");

        // ----- price bands (per copy) -----
        var bands = new[] { ("Under $1", 0.0, 1.0), ("$1 – 5", 1.0, 5.0), ("$5 – 20", 5.0, 20.0),
                            ("$20 – 100", 20.0, 100.0), ("$100 – 500", 100.0, 500.0), ("$500+", 500.0, double.MaxValue) };
        var priceBands = bands.Select(b =>
        {
            var inBand = copies.Where(c => { var v = ValueOf(c); return v >= b.Item2 && v < b.Item3; }).ToList();
            return new { label = b.Item1, copies = inBand.Count, value = Math.Round(inBand.Sum(ValueOf), 2) };
        }).ToList();

        // ----- concentration: how much of the value the top cards hold -----
        var curve = new List<object>();
        double run = 0;
        for (var i = 0; i < cardValues.Count; i++)
        {
            run += cardValues[i].Value;
            if (i == cardValues.Count - 1 || i % Math.Max(1, cardValues.Count / 40) == 0)
                curve.Add(new { cards = Math.Round((i + 1) * 100.0 / cardValues.Count, 1), value = Share(run) });
        }
        double TopShare(int n) => Share(cardValues.Take(n).Sum(x => x.Value));
        var concentration = new
        {
            cards = cardValues.Count,
            top1 = TopShare(1), top5 = TopShare(5), top10 = TopShare(10), top25 = TopShare(25),
            curve,
        };

        // ----- acquisitions by month (last 24) -----
        var sinceMonth = DateTime.UtcNow.AddMonths(-23).ToString("yyyy-MM");
        var acquisitions = copies
            .GroupBy(c => OwnedDate(c)[..7])
            .Where(g => string.CompareOrdinal(g.Key, sinceMonth) >= 0)
            .OrderBy(g => g.Key)
            .Select(g => new
            {
                month = g.Key,
                copies = g.Count(),
                value = Math.Round(g.Sum(ValueOf), 2),
                spent = Math.Round(g.Where(c => c.Source == AcquireSource.Paid).Sum(c => c.PurchasePrice ?? 0), 2),
            })
            .ToList();

        // ----- last 30 days -----
        var moved = copies.Select(c => (Now: ValueOf(c), Ago: AgoOf(c))).Where(x => x.Ago is > 0).ToList();
        var momentum = new
        {
            copies = moved.Count,
            up = moved.Count(x => x.Now > x.Ago * 1.005),
            down = moved.Count(x => x.Now < x.Ago * 0.995),
            flat = moved.Count(x => x.Now <= x.Ago * 1.005 && x.Now >= x.Ago * 0.995),
            netUsd = Math.Round(moved.Sum(x => x.Now - x.Ago!.Value), 2),
            netPct = moved.Sum(x => x.Ago!.Value) is var agoSum && agoSum > 0
                ? Math.Round((moved.Sum(x => x.Now) / agoSum - 1) * 100, 1) : 0,
        };

        // ----- model outlook: the collection's forecast at 6m and 12m -----
        var outlook = new Dictionary<string, object>();
        foreach (var horizon in new[] { "6m", "12m" })
        {
            double now = 0, fcst = 0; int n = 0, up = 0, down = 0;
            foreach (var gameGroup in copies.GroupBy(c => c.Game))
            {
                var ids = gameGroup.Select(c => c.ProductId).Distinct().ToList();
                var rows = await predictions.Forecasts
                    .Where(f => f.Game == gameGroup.Key && ids.Contains(f.ProductId) && f.Horizon == horizon)
                    .Select(f => new { f.ProductId, f.Target, f.BasePrice, f.ForecastPrice })
                    .ToListAsync();
                var byKey = rows.GroupBy(r => (r.ProductId, r.Target)).ToDictionary(g => g.Key, g => g.First());
                foreach (var c in gameGroup)
                {
                    if (!byKey.TryGetValue((c.ProductId, GradeTiers.ForecastTarget(c.Grade)), out var f) || f.BasePrice <= 0) continue;
                    var v = ValueOf(c); if (v <= 0) v = f.BasePrice;
                    var expected = v * f.ForecastPrice / f.BasePrice;   // the model's move applied to today's price
                    now += v; fcst += expected; n++;
                    if (expected > v * 1.005) up++; else if (expected < v * 0.995) down++;
                }
            }
            outlook[horizon] = new
            {
                copies = n, now = Math.Round(now, 2), forecast = Math.Round(fcst, 2),
                changeUsd = Math.Round(fcst - now, 2),
                changePct = now > 0 ? Math.Round((fcst / now - 1) * 100, 1) : 0,
                up, down,
            };
        }

        // ----- holding time -----
        var todayDate = DateTime.UtcNow.Date;
        var held = copies.Select(c => (c, Days: (todayDate - DateTime.Parse(OwnedDate(c))).TotalDays)).ToList();
        var daysSorted = held.Select(h => h.Days).OrderBy(d => d).ToList();
        var oldest = held.OrderByDescending(h => h.Days).First();
        var holding = new
        {
            avgDays = Math.Round(held.Average(h => h.Days)),
            medianDays = Math.Round(daysSorted[daysSorted.Count / 2]),
            oldest = new { name = MetaOf(oldest.c)?.Name, game = oldest.c.Game, id = oldest.c.ProductId, days = Math.Round(oldest.Days), since = OwnedDate(oldest.c) },
            firstDate = copies.Select(OwnedDate).Min(StringComparer.Ordinal),
        };

        // ----- the cards that matter most -----
        var topCards = cardValues
            .Where(x => x.Value > 0 && meta.ContainsKey((x.Game, x.Id)))   // priced, known cards only
            .Take(8).Select(x => new
        {
            x.Game, id = x.Id, name = meta.GetValueOrDefault((x.Game, x.Id))?.Name,
            setName = meta.GetValueOrDefault((x.Game, x.Id))?.SetName,
            pictureUrl = CardImageUrl(x.Game, x.Id), value = Math.Round(x.Value, 2), x.Copies, pct = Share(x.Value),
        }).ToList();

        return Ok(new
        {
            copies = copies.Count,
            cards = cardValues.Count,
            games = copies.Select(c => c.Game).Distinct().Count(),
            totalValue = Math.Round(total, 2),
            avgValue = cardValues.Count > 0 ? Math.Round(total / cardValues.Count, 2) : 0,
            medianValue = Math.Round(median, 2),
            packCopies = copies.Count(c => c.Source == AcquireSource.Pack),
            paidCopies = paidCopies.Count,
            invested = Math.Round(invested, 2),
            unrealizedUsd = Math.Round(paidValue - invested, 2),
            unrealizedPct = invested > 0 ? Math.Round((paidValue / invested - 1) * 100, 1) : (double?)null,
            realizedUsd = sold.Count > 0 ? Math.Round(realizedPl, 2) : (double?)null,
            soldCopies = sold.Count,
            byGame, byTier, bySet, byRarity, priceBands, concentration, acquisitions, momentum, outlook, holding, topCards,
        });
    }

    [HttpGet("summary")]
    public async Task<IActionResult> GetSummary(
        [FromQuery] string? game = null, [FromQuery] bool includePacks = true)
    {
        var user = User.Identity!.Name!;
        var copiesQuery = store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Owned);
        if (!string.IsNullOrEmpty(game) && game != "all")
        {
            var key = GameRegistry.KeyOrDefault(game);
            copiesQuery = copiesQuery.Where(x => x.Game == key);
        }
        var copies = await copiesQuery.ToListAsync();
        var packCopies = copies.Count(c => c.Source == AcquireSource.Pack);
        if (!includePacks)
            copies = copies.Where(c => c.Source == AcquireSource.Paid).ToList();

        // ----- Realized: the Sold track (2026-10-24), same game scope -----
        // Proceeds and P/L of every copy the user has marked sold. Pack
        // pulls carry no basis, so their proceeds count as pure gain; the
        // count is reported so the client can say so.
        var soldQuery = store.TrackedCards
            .Where(x => x.UserName == user && x.Kind == TrackKind.Sold);
        if (!string.IsNullOrEmpty(game) && game != "all")
        {
            var key = GameRegistry.KeyOrDefault(game);
            soldQuery = soldQuery.Where(x => x.Game == key);
        }
        var sold = await soldQuery.ToListAsync();
        object? realized = null;
        if (sold.Count > 0)
        {
            var proceeds = sold.Sum(c => c.SoldPrice ?? 0);
            var basis = sold.Where(c => c.Source == AcquireSource.Paid).Sum(c => c.PurchasePrice ?? 0);
            realized = new
            {
                count = sold.Count,
                proceeds = Math.Round(proceeds, 2),
                basis = Math.Round(basis, 2),
                plUsd = Math.Round(proceeds - basis, 2),
                plPct = basis > 0 ? Math.Round((proceeds / basis - 1) * 100, 1) : (double?)null,
                packCopies = sold.Count(c => c.Source == AcquireSource.Pack),
            };
        }

        if (copies.Count == 0)
            return Ok(new { totalValue = 0.0, copies = 0, packCopies, realized });

        // Full monthly history for every (game, product, tier) the portfolio touches.
        var seriesByKey = new Dictionary<(string Game, int Id, string Tier), List<(string Date, double Price)>>();
        foreach (var gameGroup in copies.GroupBy(c => c.Game))
        {
            var ids = gameGroup.Select(c => c.ProductId).Distinct().ToList();
            var tiers = gameGroup.Select(c => GradeTiers.PriceTier(c.Grade)).Distinct().ToList();
            var rows = await priceCharting.History
                .Where(h => h.Game == gameGroup.Key && ids.Contains(h.ProductId) && tiers.Contains(h.Grade))
                .Select(h => new { h.ProductId, h.Grade, h.Date, h.Price })
                .ToListAsync();
            foreach (var g in rows.GroupBy(r => (r.ProductId, r.Grade)))
                seriesByKey[(gameGroup.Key, g.Key.ProductId, g.Key.Grade)] =
                    g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList();
        }

        List<(string Date, double Price)>? SeriesOf(TrackedCard c) =>
            seriesByKey.GetValueOrDefault((c.Game, c.ProductId, GradeTiers.PriceTier(c.Grade)));
        double? LatestOf(TrackedCard c) =>
            SeriesOf(c) is { Count: > 0 } s ? s[^1].Price : null;

        // ----- Headline: total value + allocation -----
        var totalValue = copies.Sum(c => LatestOf(c) ?? 0);

        // Two views of the same value: by game, and by condition/grade tier.
        List<object> Breakdown(Func<TrackedCard, string> label) => copies
            .GroupBy(label)
            .Select(g => new { label = g.Key, value = g.Sum(c => LatestOf(c) ?? 0) })
            .Where(a => a.value > 0)
            .OrderByDescending(a => a.value)
            .Select(a => (object)new
            {
                a.label,
                value = Math.Round(a.value, 2),
                pct = totalValue > 0 ? Math.Round(a.value / totalValue * 100, 1) : 0.0,
            })
            .ToList();

        var allocation = Breakdown(c => GameRegistry.Label(c.Game));
        var gradeAllocation = Breakdown(c => TierLabel(GradeTiers.PriceTier(c.Grade)));

        // ----- Value over time (ownership-gated, prices carried forward) -----
        // Each copy contributes nothing before it was OWNED — AcquiredAt (the
        // purchase date the user set, defaulting to AddedAt) — then its
        // carried-forward market price. Cards bought before the account
        // existed pull the chart's start back to the earliest acquisition, so
        // the ownership step and the S&P benchmark both begin the day the
        // dollars were actually spent, not the day the copy was typed in.
        var acctCreated = await store.Users.Where(u => u.UserName == user)
            .Select(u => u.CreatedAt).FirstAsync();
        var acctDate = acctCreated.ToString("yyyy-MM-dd");
        var today = DateTime.UtcNow.ToString("yyyy-MM-dd");
        string OwnedDate(TrackedCard c) => (c.AcquiredAt ?? c.AddedAt).ToString("yyyy-MM-dd");
        var start = copies.Select(OwnedDate).Append(acctDate).Min(StringComparer.Ordinal)!;

        var dates = seriesByKey.Values.SelectMany(s => s.Select(p => p.Date))
            .Concat(copies.Select(OwnedDate))
            .Append(start).Append(acctDate).Append(today)
            .Where(d => string.CompareOrdinal(d, start) >= 0
                     && string.CompareOrdinal(d, today) <= 0)
            .Distinct().OrderBy(d => d).ToList();

        // One forward pass: dates are ascending, so each copy just advances a
        // cursor through its own date-sorted series (prices carry forward).
        var cursors = copies
            .Select(c => (Series: SeriesOf(c), Added: OwnedDate(c), Idx: -1))
            .ToArray();
        var series = new List<(string Date, double Value)>(dates.Count);
        foreach (var date in dates)
        {
            double total = 0;
            for (var i = 0; i < cursors.Length; i++)
            {
                var s = cursors[i].Series;
                if (s == null) continue;
                while (cursors[i].Idx + 1 < s.Count &&
                       string.CompareOrdinal(s[cursors[i].Idx + 1].Date, date) <= 0)
                    cursors[i].Idx++;
                if (cursors[i].Idx >= 0 &&
                    string.CompareOrdinal(cursors[i].Added, date) <= 0)
                    total += s[cursors[i].Idx].Price;
            }
            series.Add((date, Math.Round(total, 2)));
        }

        // Month change: latest point vs the last point at least a month back
        // (the axis mixes monthly and add dates, so [^2] isn't "a month ago").
        double? monthChangeUsd = null, monthChangePct = null;
        var monthAgo = DateTime.UtcNow.AddMonths(-1).ToString("yyyy-MM-dd");
        var prev = series.LastOrDefault(p => string.CompareOrdinal(p.Date, monthAgo) <= 0);
        if (series.Count >= 2 && prev.Value > 0)
        {
            monthChangeUsd = Math.Round(series[^1].Value - prev.Value, 2);
            monthChangePct = Math.Round((series[^1].Value / prev.Value - 1) * 100, 1);
        }

        // ----- Benchmark: the same dollars put into the S&P 500 instead -----
        // Each copy "buys" SPX on its ACQUIRED date with its cost basis:
        // purchase price when known, else the card's market price at its grade
        // on the day it was bought (carried forward; the client shows this
        // disclaimer). Same day as the copy enters the value series above.
        var spxCloses = await spx.GetCloses(start);
        double Basis(TrackedCard c)
        {
            if (c.PurchasePrice is > 0) return c.PurchasePrice.Value;
            if (SeriesOf(c) is not { Count: > 0 } s) return 0;
            var owned = OwnedDate(c);
            var at = s.LastOrDefault(p => string.CompareOrdinal(p.Date, owned) <= 0);
            return at.Price > 0 ? at.Price : s[0].Price;  // brand-new card: first known price
        }
        var contribs = copies
            .Where(c => c.Source == AcquireSource.Paid)   // pack pulls spent no dollars
            .Select(c => (Added: OwnedDate(c), Basis: Basis(c)))
            .Where(l => l.Basis > 0)
            .ToList();

        // Cumulative money-in over time (each copy's basis on its add date):
        // the client subtracts this from value changes so its "change" pills
        // show appreciation, not deposits.
        var invested = new List<object>();
        double investedRun = 0;
        foreach (var g in contribs.GroupBy(l => l.Added).OrderBy(g => g.Key))
        {
            investedRun += g.Sum(l => l.Basis);
            invested.Add(new { date = g.Key, value = Math.Round(investedRun, 2) });
        }

        var lots = contribs
            .Select(l =>
            {
                // Entry = last close on/before the add date (weekend adds enter
                // at the prior close); before the first close, use the first.
                var entry = spxCloses.LastOrDefault(
                    p => string.CompareOrdinal(p.Date, l.Added) <= 0)?.Close
                    ?? spxCloses.FirstOrDefault()?.Close ?? 0;
                return (l.Added, l.Basis, Entry: entry);
            })
            .Where(l => l.Entry > 0)
            .ToList();
        // The what-if runs to the collection's last date: closes exist only for
        // trading days (and the cache can trail by a day), so cards added after
        // the last close — a weekend's scanning — were money the Invested
        // figure counted and the S&P line had not "bought" yet, which read as
        // an index loss (2026-10-25). Those lots enter at the last close (their
        // entry above) and count at face value until the next close arrives.
        var marks = spxCloses.Select(p => (p.Date, p.Close)).ToList();
        if (spxCloses.Count > 0 && series.Count > 0
            && string.CompareOrdinal(series[^1].Date, spxCloses[^1].Date) > 0)
            marks.Add((series[^1].Date, spxCloses[^1].Close));
        var benchmark = marks.Select(p => new
        {
            date = p.Date,
            value = Math.Round(lots
                .Where(l => string.CompareOrdinal(l.Added, p.Date) <= 0)
                .Sum(l => l.Basis * p.Close / l.Entry), 2),
        }).ToList();

        // ----- P/L vs what was paid (only paid copies with a purchase price) -----
        var paidCopies = copies
            .Where(c => c.Source == AcquireSource.Paid && c.PurchasePrice is > 0).ToList();
        var paid = paidCopies.Sum(c => c.PurchasePrice!.Value);
        var paidValue = paidCopies.Sum(c => LatestOf(c) ?? 0);
        object? allTime = paid > 0 ? new
        {
            paid = Math.Round(paid, 2),
            value = Math.Round(paidValue, 2),
            plUsd = Math.Round(paidValue - paid, 2),
            plPct = Math.Round((paidValue / paid - 1) * 100, 1),
        } : null;

        // ----- Best / worst position (per card+tier, needs paid data) -----
        var positions = paidCopies
            .GroupBy(c => (c.Game, c.ProductId, Tier: GradeTiers.PriceTier(c.Grade)))
            .Select(g => new
            {
                g.Key.Game,
                Id = g.Key.ProductId,
                Paid = g.Sum(c => c.PurchasePrice!.Value),
                Value = g.Sum(c => LatestOf(c) ?? 0),
            })
            .Where(p => p.Paid > 0 && p.Value > 0)
            .Select(p => (p.Game, p.Id, p.Paid, p.Value,
                          Pct: Math.Round((p.Value / p.Paid - 1) * 100, 1)))
            .OrderByDescending(p => p.Pct)
            .ToList();

        async Task<object?> Position((string Game, int Id, double Paid, double Value, double Pct)? p)
        {
            if (p is not { } pos) return null;
            var card = await sources.Find(pos.Game, pos.Id);
            return new
            {
                game = pos.Game,
                id = pos.Id,
                name = card?.Name,
                pictureUrl = CardImageUrl(pos.Game, pos.Id),
                pct = pos.Pct,
                paid = Math.Round(pos.Paid, 2),
                value = Math.Round(pos.Value, 2),
                plUsd = Math.Round(pos.Value - pos.Paid, 2),
            };
        }

        return Ok(new
        {
            totalValue = Math.Round(totalValue, 2),

            copies = copies.Count,
            monthChangeUsd,
            monthChangePct,
            allTime,
            allocation,
            gradeAllocation,
            best = await Position(positions.Count > 0 ? positions[0] : null),
            worst = await Position(positions.Count > 1 ? positions[^1] : null),
            series = series.Select(p => new { date = p.Date, value = p.Value }),
            benchmark,
            invested,
            accountCreated = acctDate,
            packCopies,
            realized,
        });
    }
}
