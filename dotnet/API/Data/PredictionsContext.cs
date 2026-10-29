using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

// Read-only context over the ML pipeline's forecasts (predictions.db, never migrated).
public class PredictionsContext(DbContextOptions<PredictionsContext> options) : DbContext(options)
{
    public DbSet<Forecast> Forecasts => Set<Forecast>();
    public DbSet<ArchivedForecast> ForecastArchive => Set<ArchivedForecast>();
    public DbSet<MarketReport> Reports => Set<MarketReport>();
    // Pre-release estimates (2026-10-10). The table is created by the first
    // model run that includes forecast_prerelease.py; CardMarketData guards
    // every read so an older predictions.db simply serves none.
    public DbSet<PrereleaseEstimate> Prerelease => Set<PrereleaseEstimate>();
    // The public scorecard (2026-10-23): keyless, one row per (game, tier, horizon).
    public DbSet<ForecastAccuracy> Accuracy => Set<ForecastAccuracy>();
    public DbSet<CardTrend> CardTrends => Set<CardTrend>();
    public DbSet<ForecastAccuracyWeekly> AccuracyWeekly => Set<ForecastAccuracyWeekly>();
    public DbSet<ForecastAccuracyMonthly> AccuracyMonthly => Set<ForecastAccuracyMonthly>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<PrereleaseEstimate>(e =>
        {
            e.ToTable("prerelease_estimates");
            e.HasKey(x => new { x.Game, x.ProductId, x.Printing });
            e.HasQueryFilter(x => x.Printing == "");
            e.Property(x => x.Game).HasColumnName("game");
            e.Property(x => x.ProductId).HasColumnName("product_id");
            e.Property(x => x.Printing).HasColumnName("printing");
            e.Property(x => x.ReleaseDate).HasColumnName("release_date");
            e.Property(x => x.AsOf).HasColumnName("as_of");
            e.Property(x => x.Predicted).HasColumnName("predicted");
            e.Property(x => x.Low).HasColumnName("low");
            e.Property(x => x.High).HasColumnName("high");
            e.Property(x => x.Confidence).HasColumnName("confidence");
            e.Property(x => x.Reason).HasColumnName("reason");
            e.Property(x => x.ModelVersion).HasColumnName("model_version");
            e.Property(x => x.NTrain).HasColumnName("n_train");
            e.Property(x => x.ValSets).HasColumnName("val_sets");
            e.Property(x => x.ValMissPct).HasColumnName("val_miss_pct");
            e.Property(x => x.ValWithin50).HasColumnName("val_within50");
            e.Property(x => x.ScoredAt).HasColumnName("scored_at");
        });

        builder.Entity<ArchivedForecast>(f =>
        {
            f.ToTable("forecast_archive");
            f.HasKey(x => new { x.Game, x.ProductId, x.Printing, x.Target, x.Horizon, x.AsOf });
            // Base printing by default; per-printing paths opt out explicitly.
            f.HasQueryFilter(x => x.Printing == "");
            f.Property(x => x.Printing).HasColumnName("printing");
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

        builder.Entity<CardTrend>(t =>
        {
            t.ToTable("card_trends");
            t.HasNoKey();
            t.Property(x => x.Game).HasColumnName("game");
            t.Property(x => x.Grade).HasColumnName("grade");
            t.Property(x => x.ProductId).HasColumnName("product_id");
            t.Property(x => x.LatestDate).HasColumnName("latest_date");
            t.Property(x => x.LatestPrice).HasColumnName("latest_price");
            t.Property(x => x.Pct1w).HasColumnName("pct_1w");
            t.Property(x => x.Usd1w).HasColumnName("usd_1w");
            t.Property(x => x.Pct1m).HasColumnName("pct_1m");
            t.Property(x => x.Usd1m).HasColumnName("usd_1m");
            t.Property(x => x.Pct6m).HasColumnName("pct_6m");
            t.Property(x => x.Usd6m).HasColumnName("usd_6m");
            t.Property(x => x.Pct1y).HasColumnName("pct_1y");
            t.Property(x => x.Usd1y).HasColumnName("usd_1y");
            t.Property(x => x.AsOf).HasColumnName("as_of");
        });

        builder.Entity<ForecastAccuracy>(a =>
        {
            a.ToTable("forecast_accuracy");
            a.HasNoKey();
            a.Property(x => x.Game).HasColumnName("game");
            a.Property(x => x.Target).HasColumnName("target");
            a.Property(x => x.Horizon).HasColumnName("horizon");
            a.Property(x => x.N).HasColumnName("n");
            a.Property(x => x.RetMae).HasColumnName("ret_mae");
            a.Property(x => x.RetBias).HasColumnName("ret_bias");
            a.Property(x => x.BandHitRate).HasColumnName("band_hit_rate");
            a.Property(x => x.GradedThrough).HasColumnName("graded_through");
        });

        builder.Entity<ForecastAccuracyWeekly>(w =>
        {
            w.ToTable("forecast_accuracy_weekly");
            w.HasNoKey();
            w.Property(x => x.Week).HasColumnName("week");
            w.Property(x => x.N).HasColumnName("n");
            w.Property(x => x.BandHitRate).HasColumnName("band_hit_rate");
            w.Property(x => x.RetMae).HasColumnName("ret_mae");
            w.Property(x => x.FlatMae).HasColumnName("flat_mae");
            w.Property(x => x.DirN).HasColumnName("dir_n");
            w.Property(x => x.DirHitRate).HasColumnName("dir_hit_rate");
            w.Property(x => x.N10).HasColumnName("n10");
            w.Property(x => x.BandHitRate10).HasColumnName("band_hit_rate10");
            w.Property(x => x.RetMae10).HasColumnName("ret_mae10");
            w.Property(x => x.FlatMae10).HasColumnName("flat_mae10");
        });

        builder.Entity<ForecastAccuracyMonthly>(m =>
        {
            m.ToTable("forecast_accuracy_monthly");
            m.HasNoKey();
            m.Property(x => x.Game).HasColumnName("game");
            m.Property(x => x.Target).HasColumnName("target");
            m.Property(x => x.Horizon).HasColumnName("horizon");
            m.Property(x => x.Month).HasColumnName("month");
            m.Property(x => x.N).HasColumnName("n");
            m.Property(x => x.RetMae).HasColumnName("ret_mae");
            m.Property(x => x.RetBias).HasColumnName("ret_bias");
            m.Property(x => x.BandHitRate).HasColumnName("band_hit_rate");
            m.Property(x => x.GradedThrough).HasColumnName("graded_through");
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
            r.Property(x => x.Tags).HasColumnName("tags");
        });

        builder.Entity<Forecast>(f =>
        {
            f.ToTable("forecasts");
            f.HasKey(x => new { x.Game, x.ProductId, x.Printing, x.Target, x.Horizon });
            f.HasQueryFilter(x => x.Printing == "");
            f.Property(x => x.Printing).HasColumnName("printing");
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
