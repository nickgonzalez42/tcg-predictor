namespace API.DTOS;

public class TrackedCardDto
{
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public string? Kind { get; set; }   // owned | wishlist (defaults to wishlist)
    public string? Grade { get; set; }  // owned only: the copy's condition (copy-grade vocab)
}

// One owned physical copy, returned with an owned card (CardDto.OwnedCopies).
public class OwnedCopyDto
{
    public int Id { get; set; }
    public string? Grade { get; set; }
    public double? PurchasePrice { get; set; }
    public DateTime? AcquiredAt { get; set; }
    public string? Note { get; set; }
    public DateTime AddedAt { get; set; }
}

// Set how many copies of a card the user owns at one condition
// (PUT /watchlist/owned/quantity). Only blank copies are added/removed;
// copies with purchase detail are never auto-deleted.
public class SetOwnedQuantityDto
{
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public string? Grade { get; set; }   // condition (copy vocab); null/blank = unspecified bucket
    public int Quantity { get; set; }
}

// Editable per-copy fields (PATCH /watchlist/owned/{id}). All optional; a null
// field clears that value.
public class UpdateOwnedCopyDto
{
    public string? Grade { get; set; }
    public double? PurchasePrice { get; set; }
    public DateTime? AcquiredAt { get; set; }
    public string? Note { get; set; }
}

// Set (or clear, with null) a wishlist row's price alert
// (PUT /watchlist/wishlist/alert).
public class WishlistAlertDto
{
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public double? Target { get; set; }   // alert at-or-below price; null clears
}
