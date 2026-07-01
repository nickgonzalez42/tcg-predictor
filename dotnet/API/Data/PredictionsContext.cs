using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the ML pipeline's predictions.db (never migrated),
// mapped onto the snake_case `predictions` table.
public class PredictionsContext(DbContextOptions<PredictionsContext> options) : DbContext(options)
{
    public DbSet<Prediction> Predictions => Set<Prediction>();
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

        builder.Entity<Prediction>(prediction =>
        {
            prediction.ToTable("predictions");
            prediction.HasKey(x => new { x.Game, x.ProductId });
            prediction.Property(x => x.Game).HasColumnName("game");
            prediction.Property(x => x.ProductId).HasColumnName("product_id");
            prediction.Property(x => x.PredictedPrice).HasColumnName("predicted_price");
            prediction.Property(x => x.ActualPrice).HasColumnName("actual_price");
            prediction.Property(x => x.UsedImage).HasColumnName("used_image");
            prediction.Property(x => x.ModelVersion).HasColumnName("model_version");
            prediction.Property(x => x.ScoredAt).HasColumnName("scored_at");
        });
    }
}
