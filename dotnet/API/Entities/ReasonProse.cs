namespace API.Entities;

// Cached LLM-written prose summary of a card's forecast, in store.db. Generated
// on demand from the pipeline's computed forecast facts; ScoredAt ties it to the
// forecast batch it reflects, so a weekly re-score invalidates it.
public class ReasonProse
{
    public int Id { get; set; }
    public required string Game { get; set; }       // onepiece | pokemon
    public int ProductId { get; set; }
    public required string ScoredAt { get; set; }    // forecasts.scored_at this prose was built from
    public required string Prose { get; set; }
    public DateTime GeneratedAt { get; set; } = DateTime.UtcNow;
}
