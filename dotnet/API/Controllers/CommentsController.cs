using API.Data;
using API.Entities;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Card comments: threaded (ParentId), reddit-style one-vote-per-user scoring,
// automod at creation. Authors are shown by handle only — identity usernames
// (emails) never leave the server.
public class CommentsController(StoreContext store, ModerationService mod) : BaseApiController
{
    public record CreateDto(string Game, int ProductId, int? ParentId, string Body);
    public record VoteDto(int Value);   // 1, -1, or 0 (retract)

    // Comment feed for one card: flat list (client builds the tree), with
    // scores, the caller's own votes, and author handle + avatar.
    [HttpGet("{game}/{productId:int}")]
    public async Task<IActionResult> GetForCard(string game, int productId)
    {
        var key = GameRegistry.KeyOrDefault(game);
        var me = User.Identity?.IsAuthenticated == true ? User.Identity!.Name : null;

        var comments = await store.Comments
            .Where(c => c.Game == key && c.ProductId == productId && !c.Hidden)
            .OrderBy(c => c.CreatedAt)
            .ToListAsync();
        if (comments.Count == 0) return Ok(Array.Empty<object>());

        var ids = comments.Select(c => c.Id).ToList();
        var votes = await store.CommentVotes.Where(v => ids.Contains(v.CommentId)).ToListAsync();
        var scores = votes.GroupBy(v => v.CommentId).ToDictionary(g => g.Key, g => g.Sum(v => v.Value));
        var mine = me == null
            ? []
            : votes.Where(v => v.UserName == me).ToDictionary(v => v.CommentId, v => v.Value);

        var authors = comments.Select(c => c.UserName).Distinct().ToList();
        var users = await store.Users.Where(u => authors.Contains(u.UserName!))
            .Select(u => new { u.UserName, u.Handle, u.ProfilePublic, u.AvatarGame, u.AvatarProductId })
            .ToListAsync();
        var byUser = users.ToDictionary(u => u.UserName!);

        return Ok(comments.Select(c =>
        {
            byUser.TryGetValue(c.UserName, out var author);
            return new
            {
                c.Id,
                c.ParentId,
                body = c.Deleted ? null : c.Body,
                deleted = c.Deleted,
                createdAt = c.CreatedAt,
                author = c.Deleted ? null : author?.Handle,
                authorPublic = author?.ProfilePublic ?? false,
                avatarUrl = !c.Deleted && author?.AvatarGame != null && author.AvatarProductId != null
                    ? CardImageUrl(author.AvatarGame, author.AvatarProductId.Value) : null,
                score = scores.GetValueOrDefault(c.Id),
                myVote = mine.GetValueOrDefault(c.Id),
                isMine = me != null && c.UserName == me,
            };
        }));
    }

    [Authorize]
    [HttpPost]
    public async Task<IActionResult> Create(CreateDto dto)
    {
        var user = User.Identity!.Name!;
        var key = GameRegistry.Normalize(dto.Game);
        if (key == null) return BadRequest("Unknown game.");

        var profile = await store.Users.Where(u => u.UserName == user)
            .Select(u => new { u.Handle }).FirstAsync();
        if (profile.Handle == null)
            return BadRequest("Set a username in your profile before commenting.");

        // Rate limit: 5 comments/minute.
        var floor = DateTime.UtcNow.AddMinutes(-1);
        if (await store.Comments.CountAsync(c => c.UserName == user && c.CreatedAt > floor) >= 5)
            return BadRequest("You're commenting too fast — try again in a minute.");

        if (dto.ParentId is { } parentId)
        {
            var parent = await store.Comments.FirstOrDefaultAsync(c => c.Id == parentId);
            if (parent == null || parent.Game != key || parent.ProductId != dto.ProductId || parent.Hidden)
                return BadRequest("Can't reply to that comment.");
        }

        var verdict = await mod.Check(dto.Body);
        if (!verdict.Allowed) return BadRequest(verdict.Reason);

        var comment = new Comment
        {
            Game = key,
            ProductId = dto.ProductId,
            UserName = user,
            ParentId = dto.ParentId,
            Body = dto.Body.Trim(),
        };
        store.Comments.Add(comment);
        await store.SaveChangesAsync();
        return Ok(new { comment.Id });
    }

    // Author delete: keeps the thread shape ("[deleted]") if it has replies,
    // otherwise removes the row entirely.
    [Authorize]
    [HttpDelete("{id:int}")]
    public async Task<IActionResult> Delete(int id)
    {
        var comment = await store.Comments.FirstOrDefaultAsync(
            c => c.Id == id && c.UserName == User.Identity!.Name);
        if (comment == null) return NotFound();

        if (await store.Comments.AnyAsync(c => c.ParentId == id))
        {
            comment.Deleted = true;
        }
        else
        {
            store.Comments.Remove(comment);
            store.CommentVotes.RemoveRange(store.CommentVotes.Where(v => v.CommentId == id));
        }
        await store.SaveChangesAsync();
        return Ok();
    }

    [Authorize]
    [HttpPut("{id:int}/vote")]
    public async Task<IActionResult> Vote(int id, VoteDto dto)
    {
        if (dto.Value is not (-1 or 0 or 1)) return BadRequest("Vote must be -1, 0 or 1.");
        var user = User.Identity!.Name!;
        if (!await store.Comments.AnyAsync(c => c.Id == id && !c.Hidden)) return NotFound();

        var vote = await store.CommentVotes.FirstOrDefaultAsync(
            v => v.CommentId == id && v.UserName == user);
        if (dto.Value == 0)
        {
            if (vote != null) store.CommentVotes.Remove(vote);
        }
        else if (vote == null)
        {
            store.CommentVotes.Add(new CommentVote { CommentId = id, UserName = user, Value = dto.Value });
        }
        else
        {
            vote.Value = dto.Value;
        }
        await store.SaveChangesAsync();

        var score = await store.CommentVotes.Where(v => v.CommentId == id).SumAsync(v => v.Value);
        return Ok(new { score, myVote = dto.Value });
    }
}
