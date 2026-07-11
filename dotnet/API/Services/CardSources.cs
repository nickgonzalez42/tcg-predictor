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
    GundamContext gundam)
{
    public IQueryable<CardBase> Cards(string game) => game switch
    {
        "pokemon" => pokemon.Cards,
        "yugioh" => yugioh.Cards,
        "magic" => magic.Cards,
        "lorcana" => lorcana.Cards,
        "digimon" => digimon.Cards,
        "gundam" => gundam.Cards,
        _ => onePiece.Cards,
    };

    public async Task<CardBase?> Find(string game, int id) =>
        await Cards(GameRegistry.KeyOrDefault(game)).FirstOrDefaultAsync(c => c.Id == id);
}
