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

    // Owned-copy detail (all optional; null for wishlist rows).
    public string? Grade { get; set; }             // condition/grade tier (catalog vocab), null = unspecified
    public double? PurchasePrice { get; set; }     // what the user paid, USD
    public DateTime? AcquiredAt { get; set; }       // date the copy was acquired
    public string? Note { get; set; }              // freeform per-copy note

    // A copy with any purchase detail displays as its own unit and is never
    // auto-deleted by quantity changes; blank copies stack. (Get-only => unmapped.)
    public bool HasDetail => PurchasePrice != null || AcquiredAt != null || !string.IsNullOrWhiteSpace(Note);
}

public static class TrackKind
{
    public const string Owned = "owned";
    public const string Wishlist = "wishlist";

    public static string Normalize(string? kind) =>
        string.Equals(kind, Owned, StringComparison.OrdinalIgnoreCase) ? Owned : Wishlist;
}
