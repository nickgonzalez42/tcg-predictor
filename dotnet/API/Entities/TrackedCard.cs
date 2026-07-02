namespace API.Entities;

// A card a user is tracking (their watchlist). Lives in store.db alongside users.
public class TrackedCard
{
    public int Id { get; set; }
    public required string UserName { get; set; }  // owner (Identity user name / email)
    public required string Game { get; set; }      // onepiece | pokemon
    public int ProductId { get; set; }
    public DateTime AddedAt { get; set; } = DateTime.UtcNow;
}
