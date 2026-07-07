using API.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace API.Data;

// The One Piece and Pokémon card databases are produced by the scraper and are treated as
// read-only here (never migrated). Each lives in its own context so the two games stay separate.

public class OnePieceContext(DbContextOptions<OnePieceContext> options) : DbContext(options)
{
    public DbSet<OnePieceCard> Cards => Set<OnePieceCard>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<OnePieceCard>(card =>
        {
            card.ToTable("cards");
            CardMapping.MapBase(card);
            card.Property(x => x.Color).HasColumnName("color");
            card.Property(x => x.Subtypes).HasColumnName("subtypes");
            card.Property(x => x.Life).HasColumnName("life");
            card.Property(x => x.Power).HasColumnName("power");
            card.Property(x => x.Cost).HasColumnName("cost");
            card.Property(x => x.Counter).HasColumnName("counter");
            card.Property(x => x.Attribute).HasColumnName("attribute");
        });
    }
}

public class PokemonContext(DbContextOptions<PokemonContext> options) : DbContext(options)
{
    public DbSet<PokemonCard> Cards => Set<PokemonCard>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<PokemonCard>(card =>
        {
            card.ToTable("cards");
            CardMapping.MapBase(card);
            card.Property(x => x.Hp).HasColumnName("hp");
            card.Property(x => x.Stage).HasColumnName("stage");
            card.Property(x => x.EnergyType).HasColumnName("energy_type");
            card.Property(x => x.Attack1).HasColumnName("attack1");
            card.Property(x => x.Attack2).HasColumnName("attack2");
            card.Property(x => x.Attack3).HasColumnName("attack3");
            card.Property(x => x.Attack4).HasColumnName("attack4");
            card.Property(x => x.Weakness).HasColumnName("weakness");
            card.Property(x => x.Resistance).HasColumnName("resistance");
            card.Property(x => x.RetreatCost).HasColumnName("retreat_cost");
            card.Property(x => x.FlavorText).HasColumnName("flavor_text");
        });
    }
}

internal static class CardMapping
{
    // Maps the columns shared by both card databases.
    public static void MapBase<T>(EntityTypeBuilder<T> card) where T : CardBase
    {
        card.HasKey(x => x.Id);
        card.Property(x => x.Id).HasColumnName("product_id");
        card.Property(x => x.Name).HasColumnName("name");
        card.Property(x => x.SetName).HasColumnName("set_name");
        card.Property(x => x.Rarity).HasColumnName("rarity");
        card.Property(x => x.CardNumber).HasColumnName("card_number");
        card.Property(x => x.CardType).HasColumnName("card_type");
        card.Property(x => x.Description).HasColumnName("description");
        card.Property(x => x.MarketPrice).HasColumnName("market_price");
        card.Property(x => x.NearMintPrice).HasColumnName("near_mint_price");
        card.Property(x => x.ImageUrl).HasColumnName("image_url");
    }
}
