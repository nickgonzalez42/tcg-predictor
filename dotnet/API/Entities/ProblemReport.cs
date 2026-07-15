namespace API.Entities;

// A user-submitted "report a problem" message. Anyone can file one (auth
// optional); UserName is captured when signed in. Stored in store.db; a
// notification is fired on creation (see NotificationService).
public class ProblemReport
{
    public int Id { get; set; }
    public required string Message { get; set; }
    public string? PageUrl { get; set; }     // where the reporter was
    public string? Email { get; set; }        // optional reply-to they provided
    public string? UserName { get; set; }     // identity name if signed in (never served)
    public string? UserAgent { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public bool Resolved { get; set; }
}
