using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class CardsController(
    OnePieceContext onePiece, PokemonContext pokemon, PredictionsContext predictions) : BaseApiController
{
    [HttpGet]
    public async Task<ActionResult<List<CardDto>>> GetCards([FromQuery] CardParams cardParams)
    {
        return IsPokemon(cardParams.Game)
            ? await Page(pokemon.Cards, cardParams, "pokemon", (c, url) => c.ToDto(url))
            : await Page(onePiece.Cards, cardParams, "onepiece", (c, url) => c.ToDto(url));
    }

    [HttpGet("{game}/{id:int}")]
    public async Task<ActionResult<CardDto>> GetCard(string game, int id)
    {
        var folder = GameKey(game);

        CardDto? dto;
        if (IsPokemon(game))
        {
            var card = await pokemon.Cards.FindAsync(id);
            dto = card?.ToDto(ImageUrl(folder, card.Id));
        }
        else
        {
            var card = await onePiece.Cards.FindAsync(id);
            dto = card?.ToDto(ImageUrl(folder, card.Id));
        }

        if (dto == null) return NotFound();

        await AttachPredictions([dto], folder);
        return dto;
    }

    [HttpGet("filters")]
    public async Task<IActionResult> GetFilters([FromQuery] string? game)
    {
        return IsPokemon(game)
            ? Ok(await Facets(pokemon.Cards))
            : Ok(await Facets(onePiece.Cards));
    }

    private async Task<List<CardDto>> Page<T>(
        IQueryable<T> source, CardParams cardParams, string folder, Func<T, string, CardDto> toDto)
        where T : CardBase
    {
        var query = source
            .Sort(cardParams.OrderBy)
            .Search(cardParams.SearchTerm)
            .Filter(cardParams.Sets, cardParams.Rarities);

        var paged = await PagedList<T>.ToPagedList(query, cardParams.PageNumber, cardParams.PageSize);

        Response.AddPaginationHeader(paged.Metadata);

        var cards = paged.Select(c => toDto(c, ImageUrl(folder, c.Id))).ToList();
        await AttachPredictions(cards, folder);
        return cards;
    }

    // Fills in each card's model-predicted price from the predictions DB.
    private async Task AttachPredictions(List<CardDto> cards, string game)
    {
        if (cards.Count == 0) return;

        var ids = cards.Select(c => c.Id).ToList();
        var byId = await predictions.Predictions
            .Where(p => p.Game == game && ids.Contains(p.ProductId))
            .ToDictionaryAsync(p => p.ProductId);

        foreach (var card in cards)
        {
            if (!byId.TryGetValue(card.Id, out var prediction)) continue;
            card.PredictedPrice = prediction.PredictedPrice;
            card.UsedImage = prediction.UsedImage;
        }
    }

    private static async Task<object> Facets<T>(IQueryable<T> source) where T : CardBase
    {
        var sets = await source.Where(x => x.SetName != null)
            .Select(x => x.SetName!).Distinct().OrderBy(x => x).ToListAsync();
        var rarities = await source.Where(x => x.Rarity != null)
            .Select(x => x.Rarity!).Distinct().OrderBy(x => x).ToListAsync();

        return new { sets, rarities };
    }

    private string ImageUrl(string folder, int id) =>
        $"{Request.Scheme}://{Request.Host}/card-images/{folder}/{id}.jpg";

    private static bool IsPokemon(string? game) =>
        string.Equals(game?.Trim(), "pokemon", StringComparison.OrdinalIgnoreCase);

    private static string GameKey(string? game) => IsPokemon(game) ? "pokemon" : "onepiece";
}
