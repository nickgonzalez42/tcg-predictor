using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the ML pipeline's forecasts (predictions.db, never migrated).
public class PredictionsContext(DbContextOptions<PredictionsContext> options) : DbContext(options)
{
    public DbSet<Forecast> Forecasts => Set<Forecast>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

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
        });
    }
}
