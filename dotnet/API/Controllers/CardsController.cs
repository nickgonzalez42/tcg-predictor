using API.Data;
using API.DTOS;
using API.Entities;
using API.Extensions;
using API.RequestHelpers;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class CardsController(OnePieceContext onePiece, PokemonContext pokemon) : BaseApiController
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
        if (IsPokemon(game))
        {
            var card = await pokemon.Cards.FindAsync(id);
            return card == null ? NotFound() : card.ToDto(ImageUrl("pokemon", card.Id));
        }
        else
        {
            var card = await onePiece.Cards.FindAsync(id);
            return card == null ? NotFound() : card.ToDto(ImageUrl("onepiece", card.Id));
        }
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

        return paged.Select(c => toDto(c, ImageUrl(folder, c.Id))).ToList();
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
}
