using API.Data;
using API.Entities;
using API.RequestHelpers;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Resolves a game key to that game's card query, so controllers never need a
// per-game branch. IQueryable is covariant, so each concrete DbSet serves as
// an IQueryable<CardBase> and EF still translates base-property access in SQL.
public class CardSources(
    OnePieceContext onePiece, PokemonContext pokemon, YugiohContext yugioh,
    MagicContext magic, LorcanaContext lorcana, DigimonContext digimon,
    GundamContext gundam, StarwarsContext starwars,
    FleshAndBloodContext fleshAndBlood, SorceryContext sorcery,
    RiftboundContext riftbound, CyberpunkContext cyberpunk)
{
    private DbContext Context(string game) => game switch
    {
        "pokemon" => pokemon,
        "yugioh" => yugioh,
        "magic" => magic,
        "lorcana" => lorcana,
        "digimon" => digimon,
        "gundam" => gundam,
        "starwars" => starwars,
        "fleshandblood" => fleshAndBlood,
        "sorcery" => sorcery,
        "riftbound" => riftbound,
        "cyberpunk" => cyberpunk,
        _ => onePiece,
    };

    public IQueryable<CardBase> Cards(string game) => Context(game) switch
    {
        PokemonContext p => p.Cards,
        OnePieceContext o => o.Cards,
        GenericCardContext g => g.Cards,
        var other => throw new InvalidOperationException($"no card set on {other.GetType().Name}"),
    };

    // Whether this game's card table carries release_date. One Piece's older
    // scraper schema does not (its entity ignores the property), so anything
    // that ranks sets by date needs a fallback there. Read from the EF model
    // rather than a game list, so a schema catch-up switches it on by itself.
    public bool HasReleaseDates(string game) =>
        Context(game).Model.GetEntityTypes()
            .Any(t => t.FindProperty(nameof(CardBase.ReleaseDate)) != null);

    public async Task<CardBase?> Find(string game, int id) =>
        await Cards(GameRegistry.KeyOrDefault(game)).FirstOrDefaultAsync(c => c.Id == id);
}
