using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// The signed-in user's tracked lists (owned / wishlist), served with the same
// search/filter/sort/pagination vocabulary as the catalog.
public partial class CardsController
{
    [Authorize]
    [HttpGet("tracked")]
    public async Task<ActionResult<List<CardDto>>> GetTracked([FromQuery] CardParams cardParams, [FromQuery] string? kind)
    {
        var listKind = TrackKind.Normalize(kind);
        var game = GameRegistry.KeyOrDefault(cardParams.Game);

        // Owned is shown one tile per (card + condition) with a quantity, so it has
        // its own paging path. Wishlist is one tile per card.
        if (listKind == TrackKind.Owned)
            return await PageOwnedByCondition(sources.Cards(game), cardParams, game);

        var user = User.Identity!.Name!;
        var tracked = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.Kind == listKind)
            .OrderByDescending(x => x.AddedAt)
            .Select(x => new { x.ProductId, x.WatchedAtPrice, x.AddedAt })
            .ToListAsync();
        var trackedIds = tracked.Select(x => x.ProductId).ToList();

        var cards = await PageScoped(sources.Cards(game), cardParams, game, trackedIds);

        // Wishlist rows carry their watch-time price and alert target. The
        // stored snapshot is the NM price at watch time; when a graded tier is
        // shown, "watched at" instead reads that tier's history at-or-before
        // the watch date, so the column (and its "since added" %) compares
        // like with like rather than a graded price against a raw snapshot.
        var byId = tracked.ToDictionary(x => x.ProductId);
        var tier = GradeTiers.PriceTier(cardParams.Grade);
        Dictionary<int, List<(string Date, double Price)>>? tierHist = null;
        if (tier != "ungraded" && cards.Count > 0)
        {
            var ids = cards.Select(c => c.Id).ToList();
            tierHist = (await priceCharting.History
                .Where(h => h.Game == game && h.Grade == tier && ids.Contains(h.ProductId))
                .Select(h => new { h.ProductId, h.Date, h.Price })
                .ToListAsync())
                .GroupBy(h => h.ProductId)
                .ToDictionary(g => g.Key,
                    g => g.OrderBy(r => r.Date).Select(r => (r.Date, r.Price)).ToList());
        }
        foreach (var card in cards)
            if (byId.TryGetValue(card.Id, out var t))
            {
                card.WatchedAtPrice = t.WatchedAtPrice;
                if (tierHist != null)
                {
                    // Last tier price at-or-before the watch date; none = no value.
                    var cutoff = t.AddedAt.ToString("yyyy-MM-dd");
                    double? at = null;
                    if (tierHist.TryGetValue(card.Id, out var series))
                        foreach (var p in series)
                        {
                            if (string.CompareOrdinal(p.Date, cutoff) > 0) break;
                            at = p.Price;
                        }
                    card.WatchedAtPrice = at;
                }
                card.WatchedSince = t.AddedAt;
            }

        return cards;
    }

    // Like Page, but restricted to a specific set of product ids (the user's tracked
    // list). The default sort follows the tracked order (AddedAt lives in store.db, so
    // it's applied in memory); explicit sorts (name/price) work as in the catalog.
    private async Task<List<CardDto>> PageScoped(
        IQueryable<CardBase> source, CardParams p, string folder, List<int> orderedIds)
    {
        var matched = orderedIds.Count == 0
            ? []
            : await source
                .Where(c => orderedIds.Contains(c.Id))
                .Search(p.SearchTerm)
                .Filter(p.Sets, p.Rarities)
                .ToListAsync();

        var rank = new Dictionary<int, int>();
        for (var i = 0; i < orderedIds.Count; i++) rank[orderedIds[i]] = i;

        // Price sort must key off the SAME value we display: the selected grade tier's
        // price when one is shown, otherwise the Near Mint column.
        var gradePrices = string.IsNullOrEmpty(p.Grade)
            ? null
            : await market.LatestTierPrices(folder, p.Grade, matched.Select(c => c.Id).ToList());
        double? PriceOf(CardBase c) => gradePrices != null
            ? (gradePrices.TryGetValue(c.Id, out var v) ? v : null)
            : c.NearMintPrice;

        // Sorting is by ACTUAL past growth (history), MODEL forecast growth, or a
        // plain field — same vocabulary as the catalog, restricted to the tracked ids.
        var historySort = CardSorts.History(p.OrderBy);
        var forecastSort = historySort is null ? CardSorts.Forecast(p.OrderBy) : null;
        var ids = matched.Select(c => c.Id).ToList();
        var histChanges = historySort is { } h
            ? await market.HistoryChanges(folder, GradeTiers.PriceTier(p.Grade ?? ""), ids, h.Window)
            : null;
        var fcstChanges = forecastSort is { } f
            ? await market.ForecastChanges(folder, GradeTiers.ForecastTarget(p.Grade), f.Horizon, ids)
            : null;

        List<CardBase> ordered;
        if (historySort is { } hs && histChanges != null)
        {
            double? Key(CardBase c) => histChanges.TryGetValue(c.Id, out var ch)
                ? (hs.Metric == "pct" ? ch.Pct : ch.Usd) : null;
            var withChg = matched.Where(c => Key(c) != null);
            var noHistory = matched.Where(c => Key(c) == null);   // no history -> end
            ordered = (hs.Descending ? withChg.OrderByDescending(Key) : withChg.OrderBy(Key))
                .Concat(noHistory).ToList();
        }
        else if (forecastSort is { } fs && fcstChanges != null)
        {
            double Key(CardBase c) => fcstChanges.TryGetValue(c.Id, out var ch)
                ? (fs.Metric == "pct" ? ch.Pct : ch.Usd)
                : (fs.Descending ? double.NegativeInfinity : double.PositiveInfinity);  // no forecast -> end
            ordered = (fs.Descending ? matched.OrderByDescending(Key) : matched.OrderBy(Key)).ToList();
        }
        else
        {
            ordered = (p.OrderBy switch
            {
                "price" => matched.OrderBy(PriceOf),
                "priceDesc" => matched.OrderByDescending(PriceOf),
                "name" => matched.OrderBy(c => c.Name),
                _ => matched.OrderBy(c => rank.GetValueOrDefault(c.Id, int.MaxValue)),  // order added
            }).ToList();
        }

        var cards = ToDtos(PageSlice(ordered, p), folder);

        await market.ApplyGradePrice(cards, folder, p.Grade);
        if (forecastSort is { } sort && fcstChanges != null)
            foreach (var card in cards)
                if (fcstChanges.TryGetValue(card.Id, out var ch)) CardMarketData.ApplyExpected(card, ch, sort);
        // A history sort trends the tiles over the sorted window (as the catalog does);
        // otherwise use the requested trend window.
        await market.ApplyMarket(cards, folder, p.Grade, historySort?.Window ?? p.Trend);
        return cards;
    }

    // Owned-copy condition vocabulary, worst-to-best, for the Condition header
    // sort ('' = unspecified sorts first, top slabs last).
    private static readonly string[] ConditionOrder =
        ["", "mp", "lp", "nm", "grade7", "grade8", "grade9", "grade95",
         "psa10", "bgs10", "cgc10", "sgc10"];

    // The Owned list, expanded to one tile per (card + condition). Each tile carries
    // its quantity and the individual copies at that condition, is priced by that
    // condition's market price, and honors the same search/filter/sort/paging.
    private async Task<List<CardDto>> PageOwnedByCondition(
        IQueryable<CardBase> source, CardParams p, string folder)
    {
        var user = User.Identity!.Name!;

        var copies = await store.TrackedCards
            .Where(x => x.UserName == user && x.Game == folder && x.Kind == TrackKind.Owned)
            .ToListAsync();

        // Card rows for the owned products, honoring search / set / rarity filters.
        var ids = copies.Select(x => x.ProductId).Distinct().ToList();
        var cardById = new Dictionary<int, CardBase>();
        if (ids.Count > 0)
        {
            var matched = await source.Where(c => ids.Contains(c.Id))
                .Search(p.SearchTerm).Filter(p.Sets, p.Rarities).ToListAsync();
            cardById = matched.ToDictionary(c => c.Id);
        }

        // Group copies into display units, dropping cards filtered out above.
        // Blank copies (no purchase detail) stack into one unit per (card, condition)
        // with a quantity; every copy with detail (paid/date/note) is its own unit.
        var units = copies
            .Where(x => cardById.ContainsKey(x.ProductId))
            .GroupBy(x => new { x.ProductId, Grade = x.Grade ?? "" })
            .SelectMany(g =>
            {
                var detailedUnits = g.Where(x => x.HasDetail).Select(x => new
                {
                    g.Key.ProductId,
                    g.Key.Grade,
                    Copies = new List<TrackedCard> { x },
                    LastAdded = x.AddedAt,
                });
                var blanks = g.Where(x => !x.HasDetail).OrderBy(x => x.AddedAt).ToList();
                return blanks.Count == 0
                    ? detailedUnits
                    : detailedUnits.Append(new
                    {
                        g.Key.ProductId,
                        g.Key.Grade,
                        Copies = blanks,
                        LastAdded = blanks.Max(x => x.AddedAt),
                    });
            })
            .ToList();

        // Latest price for each unit, keyed by its condition's price tier.
        var priceByTier = new Dictionary<string, Dictionary<int, double>>();
        foreach (var tier in units.Select(u => GradeTiers.PriceTier(u.Grade)).Distinct())
        {
            var tierIds = units.Where(u => GradeTiers.PriceTier(u.Grade) == tier).Select(u => u.ProductId).Distinct().ToList();
            priceByTier[tier] = await market.LatestTierPrices(folder, tier, tierIds);
        }
        double? UnitPrice(int pid, string grade) =>
            priceByTier[GradeTiers.PriceTier(grade)].TryGetValue(pid, out var v) ? v : null;

        // Expected forecast change per unit, priced against the tile's own condition tier.
        var forecastSort = CardSorts.Forecast(p.OrderBy);
        var changesByTarget = new Dictionary<string, Dictionary<int, ForecastChange>>();
        if (forecastSort is not null)
            foreach (var tgt in units.Select(u => GradeTiers.ForecastTarget(u.Grade)).Distinct())
            {
                var tIds = units.Where(u => GradeTiers.ForecastTarget(u.Grade) == tgt).Select(u => u.ProductId).Distinct().ToList();
                changesByTarget[tgt] = await market.ForecastChanges(folder, tgt, forecastSort.Horizon, tIds);
            }
        ForecastChange? UnitChange(int pid, string grade) =>
            changesByTarget.TryGetValue(GradeTiers.ForecastTarget(grade), out var d) && d.TryGetValue(pid, out var ch) ? ch : null;
        double ChangeKey(int pid, string grade, ForecastSort f) =>
            UnitChange(pid, grade) is { } ch
                ? (f.Metric == "pct" ? ch.Pct : ch.Usd)
                : (f.Descending ? double.NegativeInfinity : double.PositiveInfinity);  // no forecast -> end

        // Actual price-history change per unit (the Trend column header sort),
        // computed against each unit's own condition tier.
        var historySort = CardSorts.History(p.OrderBy);
        var histByTier = new Dictionary<string, Dictionary<int, HistoryChange>>();
        if (historySort is not null)
            foreach (var tier in units.Select(u => GradeTiers.PriceTier(u.Grade)).Distinct())
            {
                var tIds = units.Where(u => GradeTiers.PriceTier(u.Grade) == tier)
                    .Select(u => u.ProductId).Distinct().ToList();
                histByTier[tier] = await market.HistoryChanges(folder, tier, tIds, historySort.Window);
            }
        double HistKey(int pid, string grade, HistorySort h)
        {
            var missing = h.Descending ? double.NegativeInfinity : double.PositiveInfinity;
            return histByTier.TryGetValue(GradeTiers.PriceTier(grade), out var d) && d.TryGetValue(pid, out var ch)
                ? (h.Metric == "pct" ? ch.Pct : ch.Usd)
                : missing;
        }

        // Unit-level sort keys for the positions table's clickable headers.
        // Nulls (no paid data / no price) always sink to the end.
        double? PaidOf(List<TrackedCard> cs) =>
            cs.Any(x => x.PurchasePrice is > 0) ? cs.Sum(x => x.PurchasePrice ?? 0) : null;
        double? ValueOf(int pid, string grade, int qty) =>
            UnitPrice(pid, grade) is { } v ? v * qty : null;
        double? PlOf(int pid, string grade, List<TrackedCard> cs) =>
            PaidOf(cs) is { } paid && ValueOf(pid, grade, cs.Count) is { } val ? val - paid : null;
        int CondRank(string grade) =>
            Array.IndexOf(ConditionOrder, grade) is var i && i >= 0 ? i : int.MaxValue;

        var ordered = (forecastSort is { } fk
            ? (fk.Descending
                ? units.OrderByDescending(u => ChangeKey(u.ProductId, u.Grade, fk))
                : units.OrderBy(u => ChangeKey(u.ProductId, u.Grade, fk)))
            : historySort is { } hk
            ? (hk.Descending
                ? units.OrderByDescending(u => HistKey(u.ProductId, u.Grade, hk))
                : units.OrderBy(u => HistKey(u.ProductId, u.Grade, hk)))
            : p.OrderBy switch
            {
                "price" => units.OrderBy(u => UnitPrice(u.ProductId, u.Grade)),
                "priceDesc" => units.OrderByDescending(u => UnitPrice(u.ProductId, u.Grade)),
                "value" => units.OrderBy(u => ValueOf(u.ProductId, u.Grade, u.Copies.Count) ?? double.PositiveInfinity),
                "valueDesc" => units.OrderByDescending(u => ValueOf(u.ProductId, u.Grade, u.Copies.Count) ?? double.NegativeInfinity),
                "paid" => units.OrderBy(u => PaidOf(u.Copies) ?? double.PositiveInfinity),
                "paidDesc" => units.OrderByDescending(u => PaidOf(u.Copies) ?? double.NegativeInfinity),
                "pl" => units.OrderBy(u => PlOf(u.ProductId, u.Grade, u.Copies) ?? double.PositiveInfinity),
                "plDesc" => units.OrderByDescending(u => PlOf(u.ProductId, u.Grade, u.Copies) ?? double.NegativeInfinity),
                "qty" => units.OrderBy(u => u.Copies.Count),
                "qtyDesc" => units.OrderByDescending(u => u.Copies.Count),
                "condition" => units.OrderBy(u => CondRank(u.Grade)),
                "conditionDesc" => units.OrderByDescending(u => CondRank(u.Grade)),
                "name" => units.OrderBy(u => cardById[u.ProductId].Name),
                "nameDesc" => units.OrderByDescending(u => cardById[u.ProductId].Name),
                _ => units.OrderByDescending(u => u.LastAdded),   // order added
            }).ToList();

        var dtos = PageSlice(ordered, p)
            .Select(u =>
            {
                var card = cardById[u.ProductId];
                var dto = card.ToDto(folder, CardImageUrl(folder, card.Id));
                dto.Price = UnitPrice(u.ProductId, u.Grade);
                dto.OwnedGrade = u.Grade.Length == 0 ? null : u.Grade;
                dto.OwnedQuantity = u.Copies.Count;
                dto.OwnedCopies = u.Copies.Select(x => new OwnedCopyDto
                {
                    Id = x.Id,
                    Grade = x.Grade,
                    // Owned copies are never priceless/dateless: 0 and AddedAt
                    // stand in for legacy rows the migration backfill missed.
                    PurchasePrice = x.PurchasePrice ?? 0,
                    AcquiredAt = x.AcquiredAt ?? x.AddedAt,
                    AutoPrice = x.AutoPrice,
                    Note = x.Note,
                    AddedAt = x.AddedAt,
                }).ToList();
                if (forecastSort is { } sort && UnitChange(u.ProductId, u.Grade) is { } ch)
                    CardMarketData.ApplyExpected(dto, ch, sort);
                return dto;
            })
            .ToList();

        // Each owned unit trends against its OWN condition tier; group by the
        // effective tier so e.g. "nm" and unspecified share one query pair.
        foreach (var group in dtos.GroupBy(d =>
                     (GradeTiers.PriceTier(d.OwnedGrade), GradeTiers.ForecastTarget(d.OwnedGrade))))
            await market.ApplyMarket(group.ToList(), folder, group.First().OwnedGrade ?? "", historySort?.Window ?? p.Trend);
        return dtos;
    }
}
