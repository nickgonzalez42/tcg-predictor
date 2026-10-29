namespace API.Entities;

// One row per card featured as a mover on a given day (2026-10-29, user:
// "I don't want it to show up for consecutive days"). The ranking prefers
// cards absent from yesterday's rows, so the showcase turns over nightly
// while a card is still free to return later in the week.
//
// It lives in store.db rather than predictions.db because the pipeline
// replaces predictions.db wholesale on every push, which would wipe the
// history the very moment it is needed.
public class ShownMover
{
    public string Day { get; set; } = "";   // yyyy-MM-dd, UTC
    public string Game { get; set; } = "";
    public int ProductId { get; set; }
}
