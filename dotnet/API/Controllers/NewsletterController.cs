using System.ComponentModel.DataAnnotations;
using API.Data;
using API.Entities;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

// Weekly-report email signup. Anonymous by design; abuse is contained by
// the per-IP rate limit, a honeypot field, and the unique email index.
public class NewsletterController(StoreContext store) : BaseApiController
{
    public class SubscribeDto
    {
        [Required, EmailAddress, MaxLength(254)]
        public required string Email { get; set; }
        [MaxLength(100)]
        public string? Source { get; set; }
        // Honeypot: real users never see or fill this field. Bots do.
        public string? Website { get; set; }
    }

    [HttpPost("subscribe")]
    [EnableRateLimiting("auth")]
    public async Task<ActionResult> Subscribe(SubscribeDto dto)
    {
        if (!string.IsNullOrEmpty(dto.Website)) return Ok(new { ok = true });

        var email = dto.Email.Trim().ToLowerInvariant();
        var existing = await store.NewsletterSubscribers
            .FirstOrDefaultAsync(s => s.Email == email);
        if (existing == null)
        {
            store.NewsletterSubscribers.Add(new NewsletterSubscriber
            {
                Email = email,
                Source = dto.Source?.Trim(),
            });
            await store.SaveChangesAsync();
        }
        else if (existing.Unsubscribed)
        {
            existing.Unsubscribed = false;   // explicit resubscribe
            await store.SaveChangesAsync();
        }
        // Same answer whether new or already subscribed: no address oracle.
        return Ok(new { ok = true });
    }
}
