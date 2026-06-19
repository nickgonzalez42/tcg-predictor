using API.Entities;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

public class StoreContext(DbContextOptions options) : IdentityDbContext<User>(options)
{
    public required DbSet<Product> Products {get; set;}
    public required DbSet<Basket>  Baskets {get; set;}

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<IdentityRole>()
            .HasData(
                new IdentityRole {Id = "a63a6b47-1198-41db-b930-423e150306e8", ConcurrencyStamp = "Member", Name = "Member", NormalizedName = "MEMBER"},
                new IdentityRole {Id = "4597f9f4-24e4-466c-8740-dde2d68ed919", ConcurrencyStamp = "Admin", Name = "Admin", NormalizedName = "ADMIN"}
            );
    }
}