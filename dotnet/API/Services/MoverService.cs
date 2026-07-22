using API.Data;
using API.DTOS;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Picks the top movers by ungraded forecast change — the market ticker and the
// home page tiles. Movers are showcased visually, so a card only qualifies once
// its art has landed (ImagePath set = fetchable from the image CDN).
public class MoverService(
    CardSources sources, PredictionsContext predictions, CardMarketData market,
    PriceChartingContext priceCharting)
{
    private sealed record Pick(string Game, int ProductId, double BasePrice, double ForecastPrice);

    // imageUrl builds the absolute art URL (host-dependent, so the controller
    // supplies it). trend overrides the displayed history window (sparkline +
    // PAST pill); default stays the year-long view. perGame > 0 (with mix)
    // guarantees every game that many cards.
    public async Task<List<CardDto>> TopMovers(
        int count, string? horizon, string? trend, int perGame, Func<string, int, string> imageUrl)
    {
        count = Math.Clamp(count, 1, 24);
        perGame = Math.Clamp(perGame, 0, 6);

        // horizon=mix (the homepage hero): each card carries its own
        // horizon's forecast fields.
        if (horizon == "mix")
        {
            // perGame: every game contributes up to N cards, drawn from its
            // strongest gainers/losers per category (the per-game guarantee
            // inside PickMovers). Display order is the client's concern —
            // the hero shuffles.
            if (perGame > 0)
            {
                var all = new List<CardDto>();
                foreach (var h in new[] { "1m", "6m", "12m" })
                    all.AddRange(await PickMovers(
                        h, GameRegistry.Keys.Length * 2, guaranteeGames: true, trend, imageUrl));
                return all
                    .DistinctBy(m => (m.Game, m.Id))
                    .GroupBy(m => m.Game)
                    .SelectMany(g => g.Take(perGame))
                    .ToList();
            }

            // Legacy mix: one small slice per category, deduped; the per-game
            // guarantee is skipped and the cross-category spread supplies the
            // variety.
            var mixed = new List<CardDto>();
            foreach (var h in new[] { "1m", "6m", "12m" })
                mixed.AddRange(await PickMovers(h, Math.Max(2, count / 3), guaranteeGames: false, trend, imageUrl));
            return mixed.DistinctBy(m => (m.Game, m.Id)).ToList();
        }

        // Single ranking horizon (1m | 6m | 12m). The default 12m keeps the
        // legacy behavior where games without year-deep data fall back to 6m;
        // an explicit horizon applies to every game (all games train them).
        var hz = horizon is "1m" or "6m" ? horizon : "12m";
        return await PickMovers(hz, count, guaranteeGames: true, trend, imageUrl);
    }

    private async Task<List<CardDto>> PickMovers(
        string hz, int count, bool guaranteeGames, string? trend, Func<string, int, string> imageUrl)
    {
        var gamesWith12m = (await predictions.Forecasts
            .Where(f => f.Target == "ungraded" && f.Horizon == "12m")
            .Select(f => f.Game).Distinct().ToListAsync()).ToHashSet();

        // Small floor price so penny cards' huge percentages don't drown out
        // everything. The default 12m falls back to a game's longest horizon
        // (6m for young games — digimon/gundam have <14 months of history).
        var moverPool = predictions.Forecasts
            .Where(f => f.Target == "ungraded" && f.BasePrice >= 10);
        moverPool = hz == "12m"
            ? moverPool.Where(f => f.Horizon == "12m"
                                   || (f.Horizon == "6m" && !gamesWith12m.Contains(f.Game)))
            : moverPool.Where(f => f.Horizon == hz);
        var baseQuery = moverPool
            .Select(f => new { f.Game, f.ProductId, f.BasePrice, f.ForecastPrice });
        // 2x buffer per side: candidates without local art are filtered below.
        var gainers = await baseQuery.OrderByDescending(f => f.ForecastPrice / f.BasePrice).Take(count * 2).ToListAsync();
        var losers = await baseQuery.OrderBy(f => f.ForecastPrice / f.BasePrice).Take(count * 2).ToListAsync();

        // Balanced mix, alternating up/down: ordering purely by |change| lets the
        // triple-digit gainers crowd every loser out of the ticker. Only genuine
        // movers qualify for each side; if one side runs dry the other fills in.
        var ups = gainers.Where(f => f.ForecastPrice > f.BasePrice);
        var downs = losers.Where(f => f.ForecastPrice < f.BasePrice);
        var candidates = ups.Select((f, i) => (f, rank: i * 2))
            .Concat(downs.Select((f, i) => (f, rank: i * 2 + 1)))
            .OrderBy(x => x.rank)
            .Select(x => x.f)
            .DistinctBy(f => (f.Game, f.ProductId))
            .ToList();
        var showable = new Dictionary<string, HashSet<int>>();
        foreach (var g in candidates.Select(f => f.Game).Distinct())
            showable[g] = await ShowcaseIds(g, candidates.Where(f => f.Game == g).Select(f => f.ProductId));
        var globalPicks = candidates
            .Where(f => showable[f.Game].Contains(f.ProductId))
            .Select(f => new Pick(f.Game, f.ProductId, f.BasePrice, f.ForecastPrice))
            .ToList();

        // Every game with forecasts is GUARANTEED two movers (its strongest
        // gainer + strongest loser) so no game monopolizes the showcase; the
        // remaining slots come from the global ranking.
        var guaranteed = new List<Pick>();
        foreach (var game in guaranteeGames ? GameRegistry.Keys : Array.Empty<string>())
        {
            var gameHorizon = hz != "12m" ? hz : (gamesWith12m.Contains(game) ? "12m" : "6m");
            if (await BestMover(game, gameHorizon, gainerSide: true) is { } gainer) guaranteed.Add(gainer);
            if (await BestMover(game, gameHorizon, gainerSide: false) is { } loser) guaranteed.Add(loser);
        }

        var picked = guaranteed
            .Concat(globalPicks)
            .DistinctBy(f => (f.Game, f.ProductId))
            .Take(Math.Max(count, guaranteed.Count))
            .ToList();

        var movers = await ToCardDtos(picked, imageUrl);

        // Sparkline/trend default to the year window (6m for young games) so
        // the tiles have enough monthly points to draw; an explicit trend
        // (e.g. the homepage's PAST 1M tiles) overrides it. The headline
        // forecast fields (FcstTo/FcstHorizon) follow the ranking horizon.
        foreach (var game in GameRegistry.Keys)
            await market.ApplyMarket(movers.Where(m => m.Game == game).ToList(), game,
                trend: trend ?? (gamesWith12m.Contains(game) ? "1y" : "6m"),
                fcstOverride: hz == "12m" ? null : hz);

        return movers;
    }

    // A game's strongest gainer or loser (with art) at one horizon.
    // Progressive price floor: prefer $10+ movers, but a game whose whole
    // ungraded market sits below that (e.g. Digimon) still gets its pick.
    private async Task<Pick?> BestMover(string game, string horizon, bool gainerSide)
    {
        foreach (var floor in new[] { 10.0, 1.0 })
        {
            var q = predictions.Forecasts
                .Where(f => f.Game == game && f.Target == "ungraded" && f.Horizon == horizon
                            && f.BasePrice >= floor)
                .Where(f => gainerSide ? f.ForecastPrice > f.BasePrice : f.ForecastPrice < f.BasePrice);
            q = gainerSide
                ? q.OrderByDescending(f => f.ForecastPrice / f.BasePrice)
                : q.OrderBy(f => f.ForecastPrice / f.BasePrice);
            // Buffer because art-less and prize-card candidates are skipped.
            var top = await q.Take(10).ToListAsync();
            var ok = await ShowcaseIds(game, top.Select(f => f.ProductId));
            var hit = top.FirstOrDefault(f => ok.Contains(f.ProductId));
            if (hit != null) return new Pick(hit.Game, hit.ProductId, hit.BasePrice, hit.ForecastPrice);
        }
        return null;
    }

    // Of these product ids, the ones fit for the showcase (cross-DB, so the
    // per-game card DB is asked directly): art has landed, the card's price is
    // current (see FreshPricedIds), and the name isn't a tournament prize
    // card — those trade at outlier prices that would otherwise dominate every
    // movers list. "Place" is matched as the ordinals prize cards actually
    // use; bare "place" also hits legitimate cards (Lorcana locations,
    // Displacer Kitten, Trading Places).
    private async Task<HashSet<int>> ShowcaseIds(string game, IEnumerable<int> ids)
    {
        var list = (await FreshPricedIds(game, ids.Distinct().ToList())).ToList();
        if (list.Count == 0) return [];
        return (await sources.Cards(game).WithArt()
            .Where(c => list.Contains(c.Id))
            .Where(c => !EF.Functions.Like(c.Name!, "%tournament%")
                        && !EF.Functions.Like(c.Name!, "%winner%")
                        && !EF.Functions.Like(c.Name!, "%1st place%")
                        && !EF.Functions.Like(c.Name!, "%2nd place%")
                        && !EF.Functions.Like(c.Name!, "%3rd place%")
                        && !EF.Functions.Like(c.Name!, "%treasure cup%")
                        && !EF.Functions.Like(c.Name!, "%regional%")
                        // "finalist"/"finals" only: bare "final" would hit real
                        // cards (Final Destiny, Finally King, Final Fantasy).
                        && !EF.Functions.Like(c.Name!, "%finalist%")
                        && !EF.Functions.Like(c.Name!, "%finals%"))
            .Select(c => c.Id).ToListAsync()).ToHashSet();
    }

    // Freshness bar for the showcase: a card qualifies only if its ungraded
    // series has a price point within the last two weeks — the same series
    // whose newest date the card page shows as "as of". PriceCharting-matched
    // cards get a daily snapshot point, so they always pass; cards priced only
    // by TCGplayer refresh on the weekly sweep; cards that fell out of both
    // (delisted, no live listings, unmatched) age out and stop headlining the
    // front page. They keep their forecasts and stay in the catalog — a
    // "current price" this old just isn't showcase material.
    private async Task<HashSet<int>> FreshPricedIds(string game, List<int> ids)
    {
        if (ids.Count == 0) return [];
        var cutoff = DateTime.UtcNow.AddDays(-14).ToString("yyyy-MM-dd");
        return (await priceCharting.History
            .Where(h => h.Game == game && h.Grade == "ungraded"
                        && ids.Contains(h.ProductId)
                        && string.Compare(h.Date, cutoff) >= 0)
            .Select(h => h.ProductId).Distinct().ToListAsync()).ToHashSet();
    }

    // Join card names/sets from the right game DB, in memory (cross-DB). The
    // normal DTO mapping keeps movers identical to catalog cards (CardType
    // etc.); ApplyMarket fills the forecast fields afterwards.
    private async Task<List<CardDto>> ToCardDtos(List<Pick> picked, Func<string, int, string> imageUrl)
    {
        var movers = new List<CardDto>();
        foreach (var game in GameRegistry.Keys)
        {
            var picks = picked.Where(f => f.Game == game).ToList();
            if (picks.Count == 0) continue;
            var ids = picks.Select(f => f.ProductId).ToList();
            var cardById = (await sources.Cards(game).Where(c => ids.Contains(c.Id)).ToListAsync())
                .ToDictionary(c => c.Id);
            foreach (var pick in picks)
            {
                if (!cardById.TryGetValue(pick.ProductId, out var card)) continue;
                var dto = card.ToDto(game, imageUrl(game, card.Id));
                dto.Game = game;
                dto.Price ??= pick.BasePrice;
                movers.Add(dto);
            }
        }
        // Preserve the ranked order (the per-game join above regrouped them).
        var rank = picked.Select((f, i) => (Key: (f.Game, f.ProductId), i))
            .ToDictionary(x => x.Key, x => x.i);
        return movers.OrderBy(m => rank[(m.Game, m.Id)]).ToList();
    }
}
