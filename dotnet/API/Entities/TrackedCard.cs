namespace API.Entities;

// A card a user is tracking. Lives in store.db alongside users.
// Kind splits tracking into two independent lists: "owned" and "wishlist".
//
// Wishlist is one row per card (a card is either wished-for or not). Owned is
// one row per PHYSICAL COPY: a user can own several copies of the same card at
// different grades/conditions, so owned rows may repeat (UserName, Game,
// ProductId). The per-copy Grade/purchase fields are optional and only edited
// from the Owned page; they are always null for wishlist rows.
public class TrackedCard
{
    public int Id { get; set; }
    public required string UserName { get; set; }  // owner (Identity user name / email)
    public required string Game { get; set; }      // onepiece | pokemon
    public int ProductId { get; set; }
    public string Kind { get; set; } = TrackKind.Wishlist;  // owned | wishlist
    public DateTime AddedAt { get; set; } = DateTime.UtcNow;

    // Owned-copy detail (null for wishlist rows; owned rows always carry a
    // price and acquired date — write paths + backfill guarantee it).
    public string? Grade { get; set; }             // condition/grade tier (catalog vocab), null = unspecified
    public double? PurchasePrice { get; set; }     // cost basis, USD; 0 = unknown, never null on owned rows
    public DateTime? AcquiredAt { get; set; }      // acquisition date; defaults to AddedAt on owned rows
    public string? Note { get; set; }              // freeform per-copy note

    // Auto price: keep PurchasePrice synced to the card's market price on its
    // acquired date (0 when no data exists that far back). Off = the user
    // typed their own price.
    public bool AutoPrice { get; set; } = true;

    // Wishlist-only detail (null for owned rows).
    public double? WatchedAtPrice { get; set; }    // NM price when the card was wishlisted

    // A copy the user has personalized (manual price, note, or a hand-set
    // acquired date) displays as its own unit and is never auto-deleted by
    // quantity changes; untouched auto-priced copies stack. (Get-only => unmapped.)
    public bool HasDetail => !AutoPrice || !string.IsNullOrWhiteSpace(Note)
        || (AcquiredAt != null && AcquiredAt.Value.Date != AddedAt.Date);
}

public static class TrackKind
{
    public const string Owned = "owned";
    public const string Wishlist = "wishlist";

    public static string Normalize(string? kind) =>
        string.Equals(kind, Owned, StringComparison.OrdinalIgnoreCase) ? Owned : Wishlist;
}
