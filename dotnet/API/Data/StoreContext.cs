using API.Entities;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

public class StoreContext(DbContextOptions<StoreContext> options) : IdentityDbContext<User>(options)
{
    public DbSet<TrackedCard> TrackedCards => Set<TrackedCard>();
    public DbSet<ReasonProse> ReasonProses => Set<ReasonProse>();
    public DbSet<SpxClose> SpxCloses => Set<SpxClose>();
    public DbSet<Comment> Comments => Set<Comment>();
    public DbSet<CommentVote> CommentVotes => Set<CommentVote>();
    public DbSet<ProblemReport> ProblemReports => Set<ProblemReport>();
    public DbSet<CardAlert> CardAlerts => Set<CardAlert>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<SpxClose>().HasKey(x => x.Date);

        // Handles are unique (case-insensitive via NOCASE collation).
        builder.Entity<User>().HasIndex(x => x.Handle).IsUnique();
        builder.Entity<User>().Property(x => x.Handle).UseCollation("NOCASE");

        builder.Entity<Comment>().HasIndex(x => new { x.Game, x.ProductId, x.CreatedAt });
        builder.Entity<CommentVote>().HasIndex(x => new { x.CommentId, x.UserName }).IsUnique();

        builder.Entity<ReasonProse>()
            .HasIndex(x => new { x.Game, x.ProductId })
            .IsUnique();

        // Wishlist is one-per-card, so enforce uniqueness for wishlist rows only.
        // Owned rows are one-per-copy and may repeat (multiple copies at different
        // grades), so they are deliberately excluded from the unique constraint.
        builder.Entity<TrackedCard>()
            .HasIndex(x => new { x.UserName, x.Game, x.ProductId })
            .IsUnique()
            .HasFilter("\"Kind\" = 'wishlist'");

        // Non-unique index covering the owned list/lookup queries.
        builder.Entity<TrackedCard>()
            .HasIndex(x => new { x.UserName, x.Game, x.Kind, x.ProductId });

        // Alert lookups are per user (list page) and per user+card (modal).
        builder.Entity<CardAlert>()
            .HasIndex(x => new { x.UserName, x.Game, x.ProductId });

        builder.Entity<IdentityRole>()
            .HasData(
                new IdentityRole {Id = "a63a6b47-1198-41db-b930-423e150306e8", ConcurrencyStamp = "Member", Name = "Member", NormalizedName = "MEMBER"},
                new IdentityRole {Id = "4597f9f4-24e4-466c-8740-dde2d68ed919", ConcurrencyStamp = "Admin", Name = "Admin", NormalizedName = "ADMIN"}
            );
    }
}