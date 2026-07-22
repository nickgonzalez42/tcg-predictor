using API.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace API.Data;

// The per-game card databases are produced by the scrapers and are treated as
// read-only here (never migrated). Each game lives in its own context/file.
//
// One Piece and Pokémon predate the generic scraper and carry typed stat
// columns; every newer game uses the generic schema (shared columns + the
// stat line as a custom_attributes JSON blob) via GenericCardContext.

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

// Shared base for every generic-schema game DB; subclasses exist only so each
// game can register its own connection string in DI.
public abstract class GenericCardContext(DbContextOptions options) : DbContext(options)
{
    public DbSet<GenericCard> Cards => Set<GenericCard>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<GenericCard>(card =>
        {
            card.ToTable("cards");
            CardMapping.MapBase(card);
            card.Property(x => x.CustomAttributes).HasColumnName("custom_attributes");
        });
    }
}

public class YugiohContext(DbContextOptions<YugiohContext> options) : GenericCardContext(options);
public class MagicContext(DbContextOptions<MagicContext> options) : GenericCardContext(options);
public class LorcanaContext(DbContextOptions<LorcanaContext> options) : GenericCardContext(options);
public class DigimonContext(DbContextOptions<DigimonContext> options) : GenericCardContext(options);
public class GundamContext(DbContextOptions<GundamContext> options) : GenericCardContext(options);
public class StarwarsContext(DbContextOptions<StarwarsContext> options) : GenericCardContext(options);

internal static class CardMapping
{
    // Maps the columns shared by every card database.
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
        card.Property(x => x.NearMintPrice).HasColumnName("near_mint_price");
        card.Property(x => x.ImageUrl).HasColumnName("image_url");
        card.Property(x => x.ImagePath).HasColumnName("image_path");
    }
}
