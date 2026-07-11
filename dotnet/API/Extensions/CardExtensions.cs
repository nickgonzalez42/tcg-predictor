using System.Text.Json;
using API.DTOS;
using API.Entities;
using API.RequestHelpers;

namespace API.Extensions;

public static class CardExtensions
{
    // Cards whose art hasn't been scraped yet are stored but never served —
    // no catalog, search, detail page, movers, or facets (image_path is synced
    // with the disk by the pipeline's art-sync step).
    public static IQueryable<T> WithArt<T>(this IQueryable<T> source) where T : CardBase =>
        source.Where(c => c.ImagePath != null && c.ImagePath != "");

    // The catalog only lists cards that are fully presentable: art on disk AND
    // real price history (near_mint_price is authoritative — set only from the
    // PriceCharting series, never a stale fallback). Cards missing either stay
    // in the database and become visible the run after both land.
    public static IQueryable<T> VisibleInCatalog<T>(this IQueryable<T> source) where T : CardBase =>
        source.WithArt().Where(c => c.NearMintPrice != null);

    public static IQueryable<T> Sort<T>(this IQueryable<T> query, string? orderBy) where T : CardBase
    {
        return orderBy switch
        {
            "price" => query.OrderBy(x => x.NearMintPrice),
            "priceDesc" => query.OrderByDescending(x => x.NearMintPrice),
            _ => query.OrderBy(x => x.Name)
        };
    }

    // Range filter on the ungraded (Near Mint) price. Cards with no price are
    // excluded whenever a bound is set — "$10 and up" shouldn't list unpriced cards.
    public static IQueryable<T> PriceRange<T>(this IQueryable<T> query, double? min, double? max)
        where T : CardBase
    {
        if (min == null && max == null) return query;
        query = query.Where(x => x.NearMintPrice != null);
        if (min != null) query = query.Where(x => x.NearMintPrice >= min);
        if (max != null) query = query.Where(x => x.NearMintPrice <= max);
        return query;
    }

    public static IQueryable<T> Search<T>(this IQueryable<T> query, string? searchTerm) where T : CardBase
    {
        if (string.IsNullOrEmpty(searchTerm)) return query;

        var lower = searchTerm.Trim().ToLower();

        return query.Where(x => x.Name != null && x.Name.ToLower().Contains(lower));
    }

    public static IQueryable<T> Filter<T>(this IQueryable<T> query, string? sets, string? rarities)
        where T : CardBase
    {
        var setList = SplitLower(sets);
        var rarityList = SplitLower(rarities);

        query = query.Where(x => setList.Count == 0
            || (x.SetName != null && setList.Contains(x.SetName.ToLower())));
        query = query.Where(x => rarityList.Count == 0
            || (x.Rarity != null && rarityList.Contains(x.Rarity.ToLower())));

        return query;
    }

    private static List<string> SplitLower(string? value)
    {
        return string.IsNullOrEmpty(value)
            ? []
            : value.ToLower().Split(",", StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();
    }

    // One DTO mapping for any game: dispatches to the typed mappers for the two
    // original games, and renders every other game's JSON stat line as attributes.
    public static CardDto ToDto(this CardBase card, string game, string pictureUrl) => card switch
    {
        PokemonCard p => p.ToDto(pictureUrl),
        OnePieceCard o => o.ToDto(pictureUrl),
        GenericCard g => g.ToDto(GameRegistry.Label(game), pictureUrl),
        _ => card.ToBaseDto(GameRegistry.Label(game), pictureUrl),
    };

    // Attribute keys already promoted to real columns (or too bulky to repeat).
    private static readonly HashSet<string> SkippedAttrs = new(StringComparer.OrdinalIgnoreCase)
    {
        "number", "rarityDbName", "description", "text", "cardText", "oracleText",
        "releaseDate", "cardType", "cardTypeB", "detailNote",
    };

    public static CardDto ToDto(this GenericCard card, string gameLabel, string pictureUrl)
    {
        var dto = card.ToBaseDto(gameLabel, pictureUrl);
        if (string.IsNullOrEmpty(card.CustomAttributes)) return dto;
        try
        {
            using var doc = JsonDocument.Parse(card.CustomAttributes);
            foreach (var prop in doc.RootElement.EnumerateObject())
            {
                if (SkippedAttrs.Contains(prop.Name)) continue;
                var value = prop.Value.ValueKind switch
                {
                    JsonValueKind.String => prop.Value.GetString(),
                    JsonValueKind.Number => prop.Value.ToString(),
                    JsonValueKind.Array => string.Join(", ", prop.Value.EnumerateArray()
                        .Where(e => e.ValueKind == JsonValueKind.String)
                        .Select(e => e.GetString())),
                    _ => null,
                };
                if (!string.IsNullOrWhiteSpace(value) && value.Length <= 400)
                    dto.AddAttr(Prettify(prop.Name), value);
            }
        }
        catch (JsonException) { /* malformed blob — the shared columns still render */ }
        return dto;
    }

    // "linkRating" -> "Link Rating".
    private static string Prettify(string key)
    {
        var chars = new List<char>(key.Length + 4) { char.ToUpperInvariant(key[0]) };
        for (var i = 1; i < key.Length; i++)
        {
            if (char.IsUpper(key[i]) && !char.IsUpper(key[i - 1])) chars.Add(' ');
            chars.Add(key[i]);
        }
        return new string(chars.ToArray());
    }

    public static CardDto ToDto(this OnePieceCard card, string pictureUrl)
    {
        var dto = card.ToBaseDto("One Piece", pictureUrl);
        dto.AddAttr("Color", card.Color);
        dto.AddAttr("Cost", card.Cost);
        dto.AddAttr("Power", card.Power);
        dto.AddAttr("Life", card.Life);
        dto.AddAttr("Counter", card.Counter);
        dto.AddAttr("Attribute", card.Attribute);
        dto.AddAttr("Subtypes", card.Subtypes);
        return dto;
    }

    public static CardDto ToDto(this PokemonCard card, string pictureUrl)
    {
        var dto = card.ToBaseDto("Pokémon", pictureUrl);
        dto.AddAttr("HP", card.Hp);
        dto.AddAttr("Stage", card.Stage);
        dto.AddAttr("Energy Type", card.EnergyType);
        dto.AddAttr("Attack 1", card.Attack1);
        dto.AddAttr("Attack 2", card.Attack2);
        dto.AddAttr("Attack 3", card.Attack3);
        dto.AddAttr("Attack 4", card.Attack4);
        dto.AddAttr("Weakness", card.Weakness);
        dto.AddAttr("Resistance", card.Resistance);
        dto.AddAttr("Retreat Cost", card.RetreatCost);
        dto.AddAttr("Flavor Text", card.FlavorText);
        return dto;
    }

    private static CardDto ToBaseDto(this CardBase card, string game, string pictureUrl)
    {
        return new CardDto
        {
            Id = card.Id,
            Name = card.Name,
            Game = game,
            SetName = card.SetName,
            Rarity = card.Rarity,
            CardNumber = card.CardNumber,
            CardType = card.CardType,
            Description = card.Description,
            Price = card.NearMintPrice,   // PriceCharting-backed; no stale TCGplayer fallback
            PictureUrl = pictureUrl
        };
    }

    private static void AddAttr(this CardDto dto, string label, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value)) dto.Attributes[label] = value;
    }
}
