using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the PriceCharting import (pricecharting.db, never migrated),
// mapped onto the snake_case `pricecharting` table.
public class PriceChartingContext(DbContextOptions<PriceChartingContext> options) : DbContext(options)
{
    public DbSet<GradedPrice> GradedPrices => Set<GradedPrice>();

    // Monthly per-grade history. Mapped to graded_price_history for now; swap
    // ToTable("price_history_unified") once the blended table is built.
    public DbSet<PriceHistoryPoint> History => Set<PriceHistoryPoint>();

    // Dated NM prices from the nightly crawl (see NmDailyPoint).
    public DbSet<NmDailyPoint> NmDaily => Set<NmDailyPoint>();

    // Raw PriceCharting rows (see PcRawPoint) — cutover-bridge reads only.
    public DbSet<PcRawPoint> PcRaw => Set<PcRawPoint>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<PriceHistoryPoint>(h =>
        {
            h.ToTable("price_history_unified");   // blended TCGplayer + PriceCharting
            h.HasKey(x => new { x.Game, x.ProductId, x.Printing, x.Grade, x.Date });
            // Every query is BASE-printing by default — per-printing paths
            // opt out explicitly with IgnoreQueryFilters (Phase 3, 2026-08-09).
            h.HasQueryFilter(x => x.Printing == "");
            h.Property(x => x.Game).HasColumnName("game");
            h.Property(x => x.ProductId).HasColumnName("product_id");
            h.Property(x => x.Printing).HasColumnName("printing");
            h.Property(x => x.Grade).HasColumnName("grade");
            h.Property(x => x.Date).HasColumnName("date");
            h.Property(x => x.Price).HasColumnName("price");
            h.Property(x => x.Source).HasColumnName("source");
        });

        builder.Entity<NmDailyPoint>(p =>
        {
            p.ToTable("tcg_nm_history");
            p.HasKey(x => new { x.Game, x.ProductId, x.Printing, x.Date });
            p.HasQueryFilter(x => x.Printing == "");
            p.Property(x => x.Game).HasColumnName("game");
            p.Property(x => x.ProductId).HasColumnName("product_id");
            p.Property(x => x.Printing).HasColumnName("printing");
            p.Property(x => x.Date).HasColumnName("date");
            p.Property(x => x.Price).HasColumnName("price");
        });

        builder.Entity<PcRawPoint>(p =>
        {
            p.ToTable("graded_price_history");
            p.HasKey(x => new { x.Game, x.ProductId, x.Printing, x.Grade, x.Date });
            p.HasQueryFilter(x => x.Printing == "");
            p.Property(x => x.Game).HasColumnName("game");
            p.Property(x => x.ProductId).HasColumnName("product_id");
            p.Property(x => x.Printing).HasColumnName("printing");
            p.Property(x => x.Grade).HasColumnName("grade");
            p.Property(x => x.Date).HasColumnName("date");
            p.Property(x => x.Price).HasColumnName("price");
        });

        builder.Entity<GradedPrice>(p =>
        {
            p.ToTable("pricecharting");
            p.HasKey(x => new { x.Game, x.ProductId });
            p.Property(x => x.Game).HasColumnName("game");
            p.Property(x => x.ProductId).HasColumnName("product_id");
            p.Property(x => x.Ungraded).HasColumnName("ungraded");
            p.Property(x => x.Grade7).HasColumnName("grade7");
            p.Property(x => x.Grade8).HasColumnName("grade8");
            p.Property(x => x.Grade9).HasColumnName("grade9");
            p.Property(x => x.Grade95).HasColumnName("grade95");
            p.Property(x => x.Psa10).HasColumnName("psa10");
            p.Property(x => x.Bgs10).HasColumnName("bgs10");
            p.Property(x => x.Cgc10).HasColumnName("cgc10");
            p.Property(x => x.Sgc10).HasColumnName("sgc10");
            p.Property(x => x.SalesVolume).HasColumnName("sales_volume");
            p.Property(x => x.UpdatedAt).HasColumnName("updated_at");
        });
    }
}
