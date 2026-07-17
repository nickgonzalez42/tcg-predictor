using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the ML pipeline's forecasts (predictions.db, never migrated).
public class PredictionsContext(DbContextOptions<PredictionsContext> options) : DbContext(options)
{
    public DbSet<Forecast> Forecasts => Set<Forecast>();
    public DbSet<ArchivedForecast> ForecastArchive => Set<ArchivedForecast>();
    public DbSet<MarketReport> Reports => Set<MarketReport>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<ArchivedForecast>(f =>
        {
            f.ToTable("forecast_archive");
            f.HasKey(x => new { x.Game, x.ProductId, x.Target, x.Horizon, x.AsOf });
            f.Property(x => x.Game).HasColumnName("game");
            f.Property(x => x.ProductId).HasColumnName("product_id");
            f.Property(x => x.Target).HasColumnName("target");
            f.Property(x => x.Horizon).HasColumnName("horizon");
            f.Property(x => x.AsOf).HasColumnName("as_of");
            f.Property(x => x.BasePrice).HasColumnName("base_price");
            f.Property(x => x.ForecastPrice).HasColumnName("forecast_price");
            f.Property(x => x.Low).HasColumnName("low");
            f.Property(x => x.High).HasColumnName("high");
            f.Property(x => x.Ret).HasColumnName("ret");
            f.Property(x => x.Confidence).HasColumnName("confidence");
            f.Property(x => x.ScoredAt).HasColumnName("scored_at");
            f.Property(x => x.RealizedPrice).HasColumnName("realized_price");
            f.Property(x => x.RealizedRet).HasColumnName("realized_ret");
            f.Property(x => x.RealizedAt).HasColumnName("realized_at");
        });

        builder.Entity<MarketReport>(r =>
        {
            r.ToTable("reports");
            r.HasKey(x => x.Slug);
            r.Property(x => x.Slug).HasColumnName("slug");
            r.Property(x => x.Title).HasColumnName("title");
            r.Property(x => x.PublishedAt).HasColumnName("published_at");
            r.Property(x => x.Summary).HasColumnName("summary");
            r.Property(x => x.BodyHtml).HasColumnName("body_html");
        });

        builder.Entity<Forecast>(f =>
        {
            f.ToTable("forecasts");
            f.HasKey(x => new { x.Game, x.ProductId, x.Target, x.Horizon });
            f.Property(x => x.Game).HasColumnName("game");
            f.Property(x => x.ProductId).HasColumnName("product_id");
            f.Property(x => x.Target).HasColumnName("target");
            f.Property(x => x.Horizon).HasColumnName("horizon");
            f.Property(x => x.AsOf).HasColumnName("as_of");
            f.Property(x => x.BasePrice).HasColumnName("base_price");
            f.Property(x => x.ForecastPrice).HasColumnName("forecast_price");
            f.Property(x => x.Low).HasColumnName("low");
            f.Property(x => x.High).HasColumnName("high");
            f.Property(x => x.Ret).HasColumnName("ret");
            f.Property(x => x.Reason).HasColumnName("reason");
            f.Property(x => x.Confidence).HasColumnName("confidence");
            f.Property(x => x.ScoredAt).HasColumnName("scored_at");
            f.Property(x => x.AnchorDate).HasColumnName("anchor_date");
        });
    }
}
