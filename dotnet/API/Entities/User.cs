using API.Entities;
using Microsoft.AspNetCore.Identity;

public class User : IdentityUser
{
    // When the account was opened — the portfolio chart and its S&P 500
    // benchmark both start here. Backfilled for existing users from their
    // earliest tracked card (see AddSpxAndUserCreatedAt migration).
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    // ----- Social profile -----
    // Public display name (identity UserName is the email — never shown).
    // Required before commenting or going public. 3-24 chars [A-Za-z0-9_].
    public string? Handle { get; set; }
    public bool ProfilePublic { get; set; }          // /u/{handle} resolves at all
    public bool ShowPortfolio { get; set; }          // public profile lists positions
    public bool ShowWatchlist { get; set; }          // public profile lists watched cards
    public string? StorefrontUrl { get; set; }       // eBay/TCGplayer/etc. shop link
    // Profile image: a card the user picked (its art is the avatar).
    public string? AvatarGame { get; set; }
    public int? AvatarProductId { get; set; }

    // Opt-in: email me when one of my card alerts hits (settings page toggle).
    public bool AlertEmails { get; set; }
}