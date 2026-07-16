using System.Globalization;
using API.Data;
using API.DTOS;
using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

[Authorize]
public class WatchlistController(
    StoreContext context, CardSources sources, PriceChartingContext priceCharting) : BaseApiController
{
    // All tracked refs across both lists — the client uses Kind to know which
    // toggle (Owned / Wishlist) is active for a card.
    [HttpGet]
    public async Task<ActionResult<List<TrackedCard>>> GetWatchlist()
    {
        return await context.TrackedCards
            .Where(x => x.UserName == User.Identity!.Name)
            .OrderByDescending(x => x.AddedAt)
            .ToListAsync();
    }

    [HttpPost]
    public async Task<ActionResult> Add(TrackedCardDto dto)
    {
        var user = User.Identity!.Name!;
        var kind = TrackKind.Normalize(dto.Kind);

        // Only store canonical game keys — a display name ("One Piece") would create
        // rows invisible to every query that filters on the key.
        var game = NormalizeGame(dto.Game);
        if (game == null)
            return BadRequest($"Unknown game '{dto.Game}' — expected one of: {string.Join(", ", GameRegistry.Keys)}.");
        dto.Game = game;

        // Wishlist is one-per-card, so skip if it's already there. Owned is
        // one-per-copy: every add creates a new copy at the given condition
        // (further purchase detail is filled in later from the Owned page).
        if (kind == TrackKind.Wishlist)
        {
            var exists = await context.TrackedCards.AnyAsync(
                x => x.UserName == user && x.Game == dto.Game && x.ProductId == dto.ProductId && x.Kind == kind);
            if (exists) return Ok();
        }

        var now = DateTime.UtcNow;
        var ownedGrade = kind == TrackKind.Owned && !string.IsNullOrWhiteSpace(dto.Grade) ? dto.Grade.Trim() : null;
        context.TrackedCards.Add(new TrackedCard
        {
            UserName = user,
            Game = dto.Game,
            ProductId = dto.ProductId,
            Kind = kind,
            Grade = ownedGrade,
            // Remember the NM price at watch time so the wishlist can show "since added".
            WatchedAtPrice = kind == TrackKind.Wishlist ? await NearMintPrice(dto.Game, dto.ProductId) : null,
            AddedAt = now,
            // Owned copies always carry an acquired date + a cost basis: auto
            // price resolves the market price on the acquired date (0 = no data).
            AcquiredAt = kind == TrackKind.Owned ? now : null,
            AutoPrice = true,
            PurchasePrice = kind == TrackKind.Owned
                ? await AutoPriceOf(dto.Game, dto.ProductId, ownedGrade, now) : null,
        });
        await context.SaveChangesAsync();

        return Ok();
    }

    // Set (or clear, with a null target) the price alert on a wishlist row.
    [HttpPut("wishlist/alert")]
    public async Task<ActionResult> SetAlert(WishlistAlertDto dto)
    {
        var game = NormalizeGame(dto.Game);
        if (game == null)
            return BadRequest($"Unknown game '{dto.Game}' — expected one of: {string.Join(", ", GameRegistry.Keys)}.");
        if (dto.Target is <= 0)
            return BadRequest("Alert target must be a positive price.");

        var item = await context.TrackedCards.FirstOrDefaultAsync(
            x => x.UserName == User.Identity!.Name && x.Game == game
                 && x.ProductId == dto.ProductId && x.Kind == TrackKind.Wishlist);
        if (item == null) return NotFound();

        item.AlertTargetPrice = dto.Target;
        await context.SaveChangesAsync();

        return Ok();
    }

    // Latest Near Mint price from the game DB's denormalized column.
    private async Task<double?> NearMintPrice(string game, int productId)
    {
        var card = await sources.Find(game, productId);
        return card?.NearMintPrice;
    }

    // Set how many copies the user owns of a card at one condition. Grows by adding
    // blank copies; shrinks by removing blank copies only — copies with purchase
    // detail are never auto-deleted, so the result can floor above the request.
    [HttpPut("owned/quantity")]
    public async Task<ActionResult> SetOwnedQuantity(SetOwnedQuantityDto dto)
    {
        var game = NormalizeGame(dto.Game);
        if (game == null)
            return BadRequest($"Unknown game '{dto.Game}' — expected one of: {string.Join(", ", GameRegistry.Keys)}.");

        var user = User.Identity!.Name!;
        var grade = Blank(dto.Grade) ? null : dto.Grade!.Trim();
        var target = Math.Clamp(dto.Quantity, 0, MaxCopiesPerCondition);

        var copies = await context.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.ProductId == dto.ProductId
                        && x.Kind == TrackKind.Owned && x.Grade == grade)
            .ToListAsync();

        if (target > copies.Count)
        {
            var now = DateTime.UtcNow;
            var autoPrice = await AutoPriceOf(game, dto.ProductId, grade, now);
            for (var i = copies.Count; i < target; i++)
                context.TrackedCards.Add(new TrackedCard
                {
                    UserName = user,
                    Game = game,
                    ProductId = dto.ProductId,
                    Kind = TrackKind.Owned,
                    Grade = grade,
                    AddedAt = now,
                    AcquiredAt = now,
                    AutoPrice = true,
                    PurchasePrice = autoPrice,
                });
        }
        else if (target < copies.Count)
        {
            var blanks = copies.Where(x => !x.HasDetail).OrderByDescending(x => x.AddedAt).ToList();
            context.TrackedCards.RemoveRange(blanks.Take(copies.Count - target));
        }

        await context.SaveChangesAsync();

        var quantity = await context.TrackedCards.CountAsync(
            x => x.UserName == user && x.Game == game && x.ProductId == dto.ProductId
                 && x.Kind == TrackKind.Owned && x.Grade == grade);
        return Ok(new { quantity });
    }

    // Bulk import owned copies from a CSV (client parses the file). Each row
    // names a card by product id or by name; a name that matches several cards
    // comes back as "ambiguous" with candidates so the user can pick, then
    // resubmit that row with a concrete product id.
    [HttpPost("owned/import")]
    public async Task<ActionResult<OwnedImportResult>> ImportOwned(OwnedImportDto dto)
    {
        if (dto.Rows.Count > MaxImportRows)
            return BadRequest($"Too many rows (max {MaxImportRows}).");

        var user = User.Identity!.Name!;
        var result = new OwnedImportResult();

        for (var i = 0; i < dto.Rows.Count; i++)
        {
            var row = dto.Rows[i];
            var rr = new OwnedImportRowResult { Index = i };
            result.Rows.Add(rr);

            var game = NormalizeGame(row.Game ?? "");
            if (game == null) { rr.Message = $"Unknown game '{row.Game}'."; continue; }

            var g = row.Grade?.Trim().ToLower();
            var grade = Blank(g) || g == "ungraded" ? null : g;
            if (grade != null && !GradeTiers.Graded.Contains(grade))
            { rr.Message = $"Unknown condition '{row.Grade}'."; continue; }
            var quantity = Math.Clamp(row.Quantity < 1 ? 1 : row.Quantity, 1, MaxCopiesPerCondition);

            // Resolve the card: explicit id wins; otherwise look the name up.
            int productId;
            if (row.ProductId is > 0)
            {
                if (!await sources.Cards(game).AnyAsync(c => c.Id == row.ProductId))
                { rr.Message = $"No {game} card with id {row.ProductId}."; continue; }
                productId = row.ProductId.Value;
            }
            else if (!Blank(row.Name))
            {
                var candidates = await ResolveByName(game, row.Name!.Trim());
                if (candidates.Count == 0) { rr.Message = $"No {game} card named \"{row.Name}\"."; continue; }
                if (candidates.Count > 1)
                {
                    rr.Status = "ambiguous";
                    rr.Message = $"{candidates.Count} cards match \"{row.Name}\". Pick one.";
                    rr.Candidates = candidates;
                    continue;
                }
                productId = candidates[0].ProductId;
            }
            else { rr.Message = "Row needs a card name or product id."; continue; }

            var acquired = ParseDateOrNow(row.AcquiredAt);
            await CreateOwnedCopies(user, game, productId, grade, quantity, row.PurchasePrice, acquired);
            rr.Status = "imported";
            rr.Added = quantity;
            result.Added += quantity;
        }

        await context.SaveChangesAsync();
        return Ok(result);
    }

    // Update one owned copy's optional detail (grade / purchase info). A null or
    // blank field clears that value.
    [HttpPatch("owned/{id:int}")]
    public async Task<ActionResult> UpdateCopy(int id, UpdateOwnedCopyDto dto)
    {
        var copy = await FindOwnedCopy(id);
        if (copy == null) return NotFound();

        copy.Grade = Blank(dto.Grade) ? null : dto.Grade!.Trim();
        // Acquired is never null (a cleared field resets to the added date) and
        // can't be in the future (client enforces max=today too).
        var acquired = dto.AcquiredAt ?? copy.AddedAt;
        copy.AcquiredAt = acquired > DateTime.UtcNow ? DateTime.UtcNow : acquired;
        copy.AutoPrice = dto.AutoPrice;
        // Auto: price follows the market on the acquired date (recomputed here
        // since grade/date may have changed). Manual: the typed price, never null.
        copy.PurchasePrice = dto.AutoPrice
            ? await AutoPriceOf(copy.Game, copy.ProductId, copy.Grade, copy.AcquiredAt.Value)
            : dto.PurchasePrice ?? 0;
        copy.Note = Blank(dto.Note) ? null : dto.Note!.Trim();
        await context.SaveChangesAsync();

        return Ok();
    }

    // Delete a single owned copy by id (owned removal is per-copy, not per-card).
    [HttpDelete("owned/{id:int}")]
    public async Task<ActionResult> RemoveCopy(int id)
    {
        var copy = await FindOwnedCopy(id);
        if (copy == null) return NotFound();

        context.TrackedCards.Remove(copy);
        await context.SaveChangesAsync();

        return Ok();
    }

    [HttpDelete("{kind}/{game}/{productId:int}")]
    public async Task<ActionResult> Remove(string kind, string game, int productId)
    {
        var normalized = TrackKind.Normalize(kind);

        // Owned copies are individual rows (possibly with purchase detail) — they're
        // removed by copy id or via the quantity endpoint, never "whichever matched".
        if (normalized == TrackKind.Owned)
            return BadRequest("Owned copies are removed per copy (DELETE watchlist/owned/{id}) or via the quantity endpoint.");

        var item = await context.TrackedCards.FirstOrDefaultAsync(
            x => x.UserName == User.Identity!.Name && x.Game == game
                 && x.ProductId == productId && x.Kind == normalized);

        if (item != null)
        {
            context.TrackedCards.Remove(item);
            await context.SaveChangesAsync();
        }

        return Ok();
    }

    // The card's market price at the copy's condition tier on a given date:
    // the last known history point at-or-before it, else 0 (no data that far
    // back — young games, or dates before PriceCharting tracked the game).
    private async Task<double> AutoPriceOf(string game, int productId, string? grade, DateTime acquired)
    {
        var tier = GradeTiers.PriceTier(grade);
        var date = acquired.ToString("yyyy-MM-dd");
        var price = await priceCharting.History
            .Where(h => h.Game == game && h.ProductId == productId && h.Grade == tier
                        && string.Compare(h.Date, date) <= 0)
            .OrderByDescending(h => h.Date)
            .Select(h => (double?)h.Price)
            .FirstOrDefaultAsync();
        return price ?? 0;
    }

    private Task<TrackedCard?> FindOwnedCopy(int id) =>
        context.TrackedCards.FirstOrDefaultAsync(
            x => x.Id == id && x.UserName == User.Identity!.Name && x.Kind == TrackKind.Owned);

    // Name lookup for the importer: exact (case-insensitive) matches first, and
    // only if there are none does it fall back to substring matches. One match
    // imports silently; several are returned for the user to disambiguate.
    private async Task<List<ImportCandidate>> ResolveByName(string game, string name)
    {
        var lower = name.ToLower();
        var exact = await ToCandidates(
            sources.Cards(game).Where(c => c.Name != null && c.Name.ToLower() == lower), game);
        if (exact.Count > 0) return exact;
        return await ToCandidates(
            sources.Cards(game).Where(c => c.Name != null && c.Name.ToLower().Contains(lower)), game);
    }

    private async Task<List<ImportCandidate>> ToCandidates(IQueryable<CardBase> query, string game)
    {
        var rows = await query
            .OrderBy(c => c.SetName).ThenBy(c => c.Name)
            .Take(MaxCandidates + 1)   // +1 so the client can flag "narrow your search"
            .Select(c => new { c.Id, c.Name, c.SetName, c.Rarity, c.NearMintPrice })
            .ToListAsync();
        return rows.Select(c => new ImportCandidate
        {
            Game = game, ProductId = c.Id, Name = c.Name, SetName = c.SetName,
            Rarity = c.Rarity, Price = c.NearMintPrice, ImageUrl = CardImageUrl(game, c.Id),
        }).ToList();
    }

    // Add `quantity` owned copies. A supplied price is a manual cost basis (auto
    // price off); otherwise the basis auto-resolves to the market on the acquired
    // date. Copies keep the acquired date, so past dates read as personalized.
    private async Task CreateOwnedCopies(
        string user, string game, int productId, string? grade, int quantity,
        double? purchasePrice, DateTime acquired)
    {
        var manual = purchasePrice is >= 0;
        var basis = manual ? purchasePrice!.Value : await AutoPriceOf(game, productId, grade, acquired);
        var now = DateTime.UtcNow;
        for (var i = 0; i < quantity; i++)
            context.TrackedCards.Add(new TrackedCard
            {
                UserName = user,
                Game = game,
                ProductId = productId,
                Kind = TrackKind.Owned,
                Grade = grade,
                AddedAt = now,
                AcquiredAt = acquired,
                AutoPrice = !manual,
                PurchasePrice = basis,
            });
    }

    // Parse a yyyy-MM-dd (or any invariant date) as UTC, never in the future;
    // anything blank or unparseable defaults to now.
    private static DateTime ParseDateOrNow(string? s)
    {
        if (!string.IsNullOrWhiteSpace(s) && DateTime.TryParse(
                s, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var d))
            return d > DateTime.UtcNow ? DateTime.UtcNow : d;
        return DateTime.UtcNow;
    }

    // Sanity cap for the quantity endpoint — keeps a typo from inserting a
    // pathological number of rows.
    private const int MaxCopiesPerCondition = 999;
    private const int MaxImportRows = 1000;
    private const int MaxCandidates = 12;

    private static bool Blank(string? s) => string.IsNullOrWhiteSpace(s);

    // Canonical game key or null if unrecognized.
    private static string? NormalizeGame(string game) => GameRegistry.Normalize(game);
}
