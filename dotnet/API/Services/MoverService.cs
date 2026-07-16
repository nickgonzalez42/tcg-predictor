using API.Data;
using API.DTOS;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Picks the top movers by ungraded forecast change — the market ticker and the
// home page tiles. Movers are showcased visually, so a card only qualifies once
// its scraped art is on disk.
public class MoverService(
    CardSources sources, PredictionsContext predictions, CardMarketData market, IConfiguration config)
{
    private sealed record Pick(string Game, int ProductId, double BasePrice, double ForecastPrice);

    // imageUrl builds the absolute art URL (host-dependent, so the controller
    // supplies it).
    public async Task<List<CardDto>> TopMovers(int count, string? horizon, Func<string, int, string> imageUrl)
    {
        count = Math.Clamp(count, 1, 24);

        // horizon=mix (the homepage hero): one small slice per forecast
        // category, deduped — each card carries its own horizon's forecast
        // fields. The per-game guarantee is skipped; the client round-robins
        // games and the cross-category spread supplies the variety.
        if (horizon == "mix")
        {
            var mixed = new List<CardDto>();
            foreach (var h in new[] { "1m", "6m", "12m" })
                mixed.AddRange(await PickMovers(h, Math.Max(2, count / 3), guaranteeGames: false, imageUrl));
            return mixed.DistinctBy(m => (m.Game, m.Id)).ToList();
        }

        // Single ranking horizon (1m | 6m | 12m). The default 12m keeps the
        // legacy behavior where games without year-deep data fall back to 6m;
        // an explicit horizon applies to every game (all games train them).
        var hz = horizon is "1m" or "6m" ? horizon : "12m";
        return await PickMovers(hz, count, guaranteeGames: true, imageUrl);
    }

    private async Task<List<CardDto>> PickMovers(
        string hz, int count, bool guaranteeGames, Func<string, int, string> imageUrl)
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
        var globalPicks = ups.Select((f, i) => (f, rank: i * 2))
            .Concat(downs.Select((f, i) => (f, rank: i * 2 + 1)))
            .OrderBy(x => x.rank)
            .Select(x => x.f)
            .DistinctBy(f => (f.Game, f.ProductId))
            .Where(f => HasLocalImage(f.Game, f.ProductId))
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

        // Sparkline/trend always use the year window (6m for young games) so the
        // tiles have enough monthly points to draw; the headline forecast fields
        // (FcstTo/FcstHorizon) follow the requested ranking horizon.
        foreach (var game in GameRegistry.Keys)
            await market.ApplyMarket(movers.Where(m => m.Game == game).ToList(), game,
                trend: gamesWith12m.Contains(game) ? "1y" : "6m",
                fcstOverride: hz == "12m" ? null : hz);

        return movers;
    }

    // A game's strongest gainer or loser (with art on disk) at one horizon.
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
            // Small buffer because art-less candidates are skipped.
            var hit = (await q.Take(5).ToListAsync())
                .FirstOrDefault(f => HasLocalImage(f.Game, f.ProductId));
            if (hit != null) return new Pick(hit.Game, hit.ProductId, hit.BasePrice, hit.ForecastPrice);
        }
        return null;
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

    private bool HasLocalImage(string game, int id)
    {
        var dir = config[$"CardImages:{game}"];
        if (string.IsNullOrWhiteSpace(dir)) return true;   // dirs unconfigured — don't blank the page
        return File.Exists(Path.Combine(dir, $"{id}.jpg"));
    }
}
