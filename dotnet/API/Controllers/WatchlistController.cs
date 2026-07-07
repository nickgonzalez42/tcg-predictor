using API.Data;
using API.DTOS;
using API.Entities;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

[Authorize]
public class WatchlistController(StoreContext context) : BaseApiController
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
            return BadRequest($"Unknown game '{dto.Game}' — expected 'onepiece' or 'pokemon'.");
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

        context.TrackedCards.Add(new TrackedCard
        {
            UserName = user,
            Game = dto.Game,
            ProductId = dto.ProductId,
            Kind = kind,
            Grade = kind == TrackKind.Owned && !string.IsNullOrWhiteSpace(dto.Grade) ? dto.Grade.Trim() : null,
            AddedAt = DateTime.UtcNow,
        });
        await context.SaveChangesAsync();

        return Ok();
    }

    // Set how many copies the user owns of a card at one condition. Grows by adding
    // blank copies; shrinks by removing blank copies only — copies with purchase
    // detail are never auto-deleted, so the result can floor above the request.
    [HttpPut("owned/quantity")]
    public async Task<ActionResult> SetOwnedQuantity(SetOwnedQuantityDto dto)
    {
        var game = NormalizeGame(dto.Game);
        if (game == null)
            return BadRequest($"Unknown game '{dto.Game}' — expected 'onepiece' or 'pokemon'.");

        var user = User.Identity!.Name!;
        var grade = Blank(dto.Grade) ? null : dto.Grade!.Trim();
        var target = Math.Clamp(dto.Quantity, 0, MaxCopiesPerCondition);

        var copies = await context.TrackedCards
            .Where(x => x.UserName == user && x.Game == game && x.ProductId == dto.ProductId
                        && x.Kind == TrackKind.Owned && x.Grade == grade)
            .ToListAsync();

        if (target > copies.Count)
        {
            for (var i = copies.Count; i < target; i++)
                context.TrackedCards.Add(new TrackedCard
                {
                    UserName = user,
                    Game = game,
                    ProductId = dto.ProductId,
                    Kind = TrackKind.Owned,
                    Grade = grade,
                    AddedAt = DateTime.UtcNow,
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

    // Update one owned copy's optional detail (grade / purchase info). A null or
    // blank field clears that value.
    [HttpPatch("owned/{id:int}")]
    public async Task<ActionResult> UpdateCopy(int id, UpdateOwnedCopyDto dto)
    {
        var copy = await FindOwnedCopy(id);
        if (copy == null) return NotFound();

        copy.Grade = Blank(dto.Grade) ? null : dto.Grade!.Trim();
        copy.PurchasePrice = dto.PurchasePrice;
        copy.AcquiredAt = dto.AcquiredAt;
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

    private Task<TrackedCard?> FindOwnedCopy(int id) =>
        context.TrackedCards.FirstOrDefaultAsync(
            x => x.Id == id && x.UserName == User.Identity!.Name && x.Kind == TrackKind.Owned);

    // Sanity cap for the quantity endpoint — keeps a typo from inserting a
    // pathological number of rows.
    private const int MaxCopiesPerCondition = 999;

    private static bool Blank(string? s) => string.IsNullOrWhiteSpace(s);

    // Canonical game key or null if unrecognized.
    private static string? NormalizeGame(string game)
    {
        var key = game.Trim().ToLowerInvariant();
        return key is "onepiece" or "pokemon" ? key : null;
    }
}
