namespace API.Entities;

// One comment on a card's detail page. Replies reference ParentId (arbitrary
// depth; the client renders the tree). Votes live in CommentVote; the score
// is computed at read time. Automod can hide a comment at creation.
public class Comment
{
    public int Id { get; set; }
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public required string UserName { get; set; }   // author (identity name / email — never sent to clients)
    public int? ParentId { get; set; }
    public required string Body { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public bool Deleted { get; set; }               // author-deleted: body blanks, thread keeps shape
    public bool Hidden { get; set; }                // automod-removed: never served
    public string? ModReason { get; set; }
}

// One user's vote on one comment: +1 / -1 (a retracted vote deletes the row).
public class CommentVote
{
    public int Id { get; set; }
    public int CommentId { get; set; }
    public required string UserName { get; set; }
    public int Value { get; set; }
}
