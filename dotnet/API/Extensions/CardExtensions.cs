using API.DTOS;
using API.Entities;

namespace API.Extensions;

public static class CardExtensions
{
    public static IQueryable<T> Sort<T>(this IQueryable<T> query, string? orderBy) where T : CardBase
    {
        return orderBy switch
        {
            "price" => query.OrderBy(x => x.MarketPrice),
            "priceDesc" => query.OrderByDescending(x => x.MarketPrice),
            _ => query.OrderBy(x => x.Name)
        };
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
            Price = card.MarketPrice,
            PictureUrl = pictureUrl,
            ImageUrl = card.ImageUrl
        };
    }

    private static void AddAttr(this CardDto dto, string label, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value)) dto.Attributes[label] = value;
    }
}
