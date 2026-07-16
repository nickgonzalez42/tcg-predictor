using System.Text.RegularExpressions;
using API.Data;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Social profiles: the signed-in user's settings (handle, visibility,
// storefront, avatar card) and the public /u/{handle} view, which exposes the
// watchlist and/or portfolio ONLY where the owner opted in. Identity
// usernames are emails and are never sent anywhere.
public class ProfileController(
    StoreContext store, UserManager<User> userManager,
    PriceChartingContext priceCharting, CardSources sources) : BaseApiController
{
    private static readonly Regex HandleRx = new("^[A-Za-z0-9_]{3,24}$", RegexOptions.Compiled);

    public record ProfileSettingsDto(
        string? Handle, bool ProfilePublic, bool ShowPortfolio, bool ShowWatchlist,
        string? StorefrontUrl, string? AvatarGame, int? AvatarProductId,
        bool AlertEmails = false);

    [Authorize]
    [HttpGet("me")]
    public async Task<IActionResult> GetMine()
    {
        var user = await userManager.FindByNameAsync(User.Identity!.Name!);
        if (user == null) return NotFound();
        return Ok(ToSettings(user));
    }

    [Authorize]
    [HttpPut]
    public async Task<IActionResult> Update(ProfileSettingsDto dto)
    {
        var user = await userManager.FindByNameAsync(User.Identity!.Name!);
        if (user == null) return NotFound();

        // A blank handle is ignored rather than cleared — once chosen it can't
        // be unset, because comments reference it.
        var handle = dto.Handle?.Trim();
        if (!string.IsNullOrEmpty(handle))
        {
            if (!HandleRx.IsMatch(handle))
                return BadRequest("Usernames are 3-24 characters: letters, numbers, underscores.");
            var taken = await store.Users.AnyAsync(u =>
                u.Id != user.Id && u.Handle != null && u.Handle.ToLower() == handle.ToLower());
            if (taken) return BadRequest($"'{handle}' is taken.");
            user.Handle = handle;
        }

        var storefront = dto.StorefrontUrl?.Trim();
        if (!string.IsNullOrEmpty(storefront))
        {
            if (!Uri.TryCreate(storefront, UriKind.Absolute, out var uri)
                || (uri.Scheme != "https" && uri.Scheme != "http"))
                return BadRequest("Storefront must be a full http(s) link.");
            // Only the two marketplaces the hobby actually sells on — public
            // profile links shouldn't be able to point anywhere unvetted.
            var host = uri.Host.ToLowerInvariant();
            string[] allowed = ["ebay.com", "tcgplayer.com"];
            if (!allowed.Any(d => host == d || host.EndsWith("." + d)))
                return BadRequest("Storefront must be an eBay or TCGplayer link.");
            user.StorefrontUrl = storefront;
        }
        else
        {
            user.StorefrontUrl = null;
        }

        // Avatar: any real card the user picks; its art becomes the profile image.
        if (!string.IsNullOrEmpty(dto.AvatarGame) && dto.AvatarProductId is { } pid)
        {
            var game = GameRegistry.Normalize(dto.AvatarGame);
            if (game == null || await sources.Find(game, pid) == null)
                return BadRequest("Avatar card not found.");
            user.AvatarGame = game;
            user.AvatarProductId = pid;
        }
        else if (dto.AvatarGame == null && dto.AvatarProductId == null)
        {
            user.AvatarGame = null;
            user.AvatarProductId = null;
        }

        // Going public requires a handle to be public AS.
        if ((dto.ProfilePublic || dto.ShowPortfolio || dto.ShowWatchlist) && user.Handle == null)
            return BadRequest("Set a username before making your profile public.");
        user.ProfilePublic = dto.ProfilePublic;
        user.ShowPortfolio = dto.ProfilePublic && dto.ShowPortfolio;
        user.ShowWatchlist = dto.ProfilePublic && dto.ShowWatchlist;
        user.AlertEmails = dto.AlertEmails;

        await store.SaveChangesAsync();
        return Ok(ToSettings(user));
    }

    // Public profile header. 404s unless the owner opted in. The card lists
    // themselves come from GET {handle}/cards (filtered/sorted/paginated).
    [HttpGet("{handle}")]
    public async Task<IActionResult> GetPublic(string handle)
    {
        var user = await store.Users.FirstOrDefaultAsync(u =>
            u.Handle != null && u.Handle.ToLower() == handle.ToLower());
        if (user == null || !user.ProfilePublic) return NotFound();

        var tracked = await store.TrackedCards
            .Where(t => t.UserName == user.UserName)
            .ToListAsync();

        int? watchlistCount = user.ShowWatchlist
            ? tracked.Count(t => t.Kind == TrackKind.Wishlist) : null;

        int? portfolioCount = null;
        double? totalValue = null;
        if (user.ShowPortfolio)
        {
            var rows = await PublicCards(OwnedPositions(tracked));
            portfolioCount = rows.Count;
            totalValue = Math.Round(rows.Sum(r => (r.Price ?? 0) * r.Quantity), 2);
        }

        return Ok(new
        {
            handle = user.Handle,
            joined = user.CreatedAt.ToString("yyyy-MM-dd"),
            storefrontUrl = user.StorefrontUrl,
            avatarUrl = AvatarUrl(user),
            watchlistCount,
            portfolioCount,
            totalValue,
        });
    }

    public class PublicCardsParams : PaginationParams
    {
        public string List { get; set; } = "portfolio";   // portfolio | watchlist
        public string? Game { get; set; }                 // canonical key; empty/all = every game
        public string? OrderBy { get; set; }              // name/set/condition/qty/value (+Desc)
    }

    // One shared list (portfolio or watchlist) — game-filtered, sorted by any
    // column, paginated. 404s unless the owner shares that list.
    [HttpGet("{handle}/cards")]
    public async Task<IActionResult> GetPublicCards(string handle, [FromQuery] PublicCardsParams p)
    {
        var user = await store.Users.FirstOrDefaultAsync(u =>
            u.Handle != null && u.Handle.ToLower() == handle.ToLower());
        if (user == null || !user.ProfilePublic) return NotFound();

        var wantWatchlist = string.Equals(p.List, "watchlist", StringComparison.OrdinalIgnoreCase);
        if (wantWatchlist ? !user.ShowWatchlist : !user.ShowPortfolio) return NotFound();

        var game = string.IsNullOrEmpty(p.Game) || p.Game == "all"
            ? null : GameRegistry.Normalize(p.Game);
        var tracked = await store.TrackedCards
            .Where(t => t.UserName == user.UserName
                && (game == null || t.Game == game))
            .ToListAsync();

        var refs = wantWatchlist
            ? tracked.Where(t => t.Kind == TrackKind.Wishlist)
                .Select(t => (t.Game, t.ProductId, Grade: "", Qty: 1)).ToList()
            : OwnedPositions(tracked);
        var rows = SortRows(await PublicCards(refs), p.OrderBy);

        Response.AddPaginationHeader(PaginationMetadata.For(rows.Count, p.PageNumber, p.PageSize));
        return Ok(rows.Skip((p.PageNumber - 1) * p.PageSize).Take(p.PageSize));
    }

    // Owned copies collapse to one row per (game, card, price tier), qty = copies.
    private static List<(string Game, int ProductId, string Grade, int Qty)> OwnedPositions(
        List<TrackedCard> tracked) =>
        tracked.Where(t => t.Kind == TrackKind.Owned)
            .GroupBy(t => (t.Game, t.ProductId, Grade: GradeTiers.PriceTier(t.Grade)))
            .Select(g => (g.Key.Game, g.Key.ProductId, g.Key.Grade, Qty: g.Count()))
            .ToList();

    // Keys mirror the client's sortable column headers; default is value desc.
    private static readonly string[] ConditionOrder =
        ["ungraded", "grade7", "grade8", "grade9", "grade95",
         "psa10", "bgs10", "cgc10", "sgc10"];

    private static List<PublicCardRow> SortRows(List<PublicCardRow> rows, string? orderBy)
    {
        double Value(PublicCardRow r) => (r.Price ?? 0) * r.Quantity;
        int Cond(PublicCardRow r) =>
            Array.IndexOf(ConditionOrder, r.Grade) is var i && i >= 0 ? i : int.MaxValue;
        return (orderBy switch
        {
            "name" => rows.OrderBy(r => r.Name),
            "nameDesc" => rows.OrderByDescending(r => r.Name),
            "set" => rows.OrderBy(r => r.SetName),
            "setDesc" => rows.OrderByDescending(r => r.SetName),
            "condition" => rows.OrderBy(Cond),
            "conditionDesc" => rows.OrderByDescending(Cond),
            "qty" => rows.OrderBy(r => r.Quantity),
            "qtyDesc" => rows.OrderByDescending(r => r.Quantity),
            "value" => rows.OrderBy(Value),
            _ => rows.OrderByDescending(Value),
        }).ThenBy(r => r.Name).ToList();
    }

    // Resolve a batch of (game, product, tier) rows to public display cards:
    // name/set/art plus the tier's current price.
    private async Task<List<PublicCardRow>> PublicCards(
        List<(string Game, int ProductId, string Grade, int Qty)> refs)
    {
        var outRows = new List<PublicCardRow>();
        foreach (var gameGroup in refs.GroupBy(r => r.Game))
        {
            var ids = gameGroup.Select(r => r.ProductId).Distinct().ToList();
            var cards = (await sources.Cards(gameGroup.Key)
                    .Where(c => ids.Contains(c.Id)).ToListAsync())
                .ToDictionary(c => c.Id);
            var prices = await priceCharting.GradedPrices
                .Where(p => p.Game == gameGroup.Key && ids.Contains(p.ProductId))
                .ToDictionaryAsync(p => p.ProductId);

            foreach (var r in gameGroup)
            {
                if (!cards.TryGetValue(r.ProductId, out var card)) continue;
                prices.TryGetValue(r.ProductId, out var p);
                outRows.Add(new PublicCardRow(
                    gameGroup.Key, r.ProductId, card.Name, card.SetName,
                    GradeTiers.PriceTier(r.Grade), r.Qty,
                    p?.PriceFor(GradeTiers.PriceTier(r.Grade)),
                    CardImageUrl(gameGroup.Key, r.ProductId)));
            }
        }
        return outRows;
    }

    private string? AvatarUrl(User user) =>
        user.AvatarGame != null && user.AvatarProductId != null
            ? CardImageUrl(user.AvatarGame, user.AvatarProductId.Value)
            : null;

    private object ToSettings(User user) => new
    {
        handle = user.Handle,
        profilePublic = user.ProfilePublic,
        showPortfolio = user.ShowPortfolio,
        showWatchlist = user.ShowWatchlist,
        storefrontUrl = user.StorefrontUrl,
        avatarGame = user.AvatarGame,
        avatarProductId = user.AvatarProductId,
        avatarUrl = AvatarUrl(user),
        alertEmails = user.AlertEmails,
    };

    public record PublicCardRow(
        string Game, int ProductId, string? Name, string? SetName,
        string Grade, int Quantity, double? Price, string PictureUrl);
}
