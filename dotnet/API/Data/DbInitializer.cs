using API.Entities;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

namespace API.Data;

public class DbInitializer
{
    public static async Task InitDb(WebApplication app)
    {
        using var scope = app.Services.CreateScope();

        var context = scope.ServiceProvider.GetRequiredService<StoreContext>()
            ?? throw new InvalidOperationException("Failed to retrieve store context");
        var userManager = scope.ServiceProvider.GetRequiredService<UserManager<User>>()
            ?? throw new InvalidOperationException("Failed to retrieve userManager context");

        await SeedData(context, userManager, app.Environment.IsDevelopment());
    }

    private static async Task SeedData(StoreContext context, UserManager<User> userManager,
        bool isDevelopment)
    {
        context.Database.Migrate();   // always: migrations run in every environment

        // Demo accounts (bob@/admin@ with a well-known password) are for local
        // dev ONLY. Never create them in Production — the credentials are public
        // in this repo, so seeding them there would leave known-password live
        // accounts on the server.
        if (isDevelopment && !userManager.Users.Any())
        {
            var user = new User
            {
                UserName = "bob@test.com",
                Email = "bob@test.com"
            };

            await userManager.CreateAsync(user, "Pa$$w0rd");
            await userManager.AddToRoleAsync(user, "Member");

            var admin = new User
            {
                UserName = "admin@test.com",
                Email = "admin@test.com"
            };

            await userManager.CreateAsync(admin, "Pa$$w0rd");
            await userManager.AddToRoleAsync(admin, "Member");
        }
    }
}
