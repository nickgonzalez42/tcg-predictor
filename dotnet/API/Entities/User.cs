using API.Entities;
using Microsoft.AspNetCore.Identity;

public class User : IdentityUser
{
    public int? AddressId {get;set;}
    public Address? Address {get;set;}

    // When the account was opened — the portfolio chart and its S&P 500
    // benchmark both start here. Backfilled for existing users from their
    // earliest tracked card (see AddSpxAndUserCreatedAt migration).
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
}