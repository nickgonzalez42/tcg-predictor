namespace API.Entities;

// Weekly-report email signup (2026-10-09). Capture only for now — the
// Friday send job comes once the sending domain is verified. Unsubscribed
// is flipped (never deleted) so a resubscribe can't resurrect an opt-out.
public class NewsletterSubscriber
{
    public int Id { get; set; }
    public required string Email { get; set; }      // stored lowercased
    public string? Source { get; set; }             // page that captured it
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public bool Unsubscribed { get; set; }
}
