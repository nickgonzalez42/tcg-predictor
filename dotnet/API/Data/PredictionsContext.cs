using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the ML pipeline's predictions.db (never migrated),
// mapped onto the snake_case `predictions` table.
public class PredictionsContext(DbContextOptions<PredictionsContext> options) : DbContext(options)
{
    public DbSet<Prediction> Predictions => Set<Prediction>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

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
