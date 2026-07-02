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
        var exists = await context.TrackedCards
            .AnyAsync(x => x.UserName == user && x.Game == dto.Game && x.ProductId == dto.ProductId);

        if (!exists)
        {
            context.TrackedCards.Add(new TrackedCard
            {
                UserName = user,
                Game = dto.Game,
                ProductId = dto.ProductId,
                AddedAt = DateTime.UtcNow,
            });
            await context.SaveChangesAsync();
        }

        return Ok();
    }

    [HttpDelete("{game}/{productId:int}")]
    public async Task<ActionResult> Remove(string game, int productId)
    {
        var item = await context.TrackedCards.FirstOrDefaultAsync(
            x => x.UserName == User.Identity!.Name && x.Game == game && x.ProductId == productId);

        if (item != null)
        {
            context.TrackedCards.Remove(item);
            await context.SaveChangesAsync();
        }

        return Ok();
    }
}
