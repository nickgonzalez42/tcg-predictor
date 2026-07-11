namespace API.RequestHelpers;

// The games the API serves — mirrors pipeline/games.py. Keys are the canonical
// ids used in routes, the tracked-cards table, predictions.db, and the image
// folders; labels are what card DTOs display.
public static class GameRegistry
{
    public static readonly string[] Keys =
        ["onepiece", "pokemon", "yugioh", "magic", "lorcana", "digimon", "gundam"];

    private static readonly Dictionary<string, string> Labels = new()
    {
        ["onepiece"] = "One Piece",
        ["pokemon"] = "Pokémon",
        ["yugioh"] = "Yu-Gi-Oh!",
        ["magic"] = "Magic",
        ["lorcana"] = "Lorcana",
        ["digimon"] = "Digimon",
        ["gundam"] = "Gundam",
    };

    public static string Label(string key) => Labels.GetValueOrDefault(key, key);

    // Canonical key, or null if unrecognized.
    public static string? Normalize(string? game)
    {
        var key = game?.Trim().ToLowerInvariant();
        return key != null && Labels.ContainsKey(key) ? key : null;
    }

    // Key for query params that default rather than error.
    public static string KeyOrDefault(string? game) => Normalize(game) ?? "onepiece";
}
