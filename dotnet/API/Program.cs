using Microsoft.EntityFrameworkCore;
using API.Data;
using API.Middleware;
using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.FileProviders;

var builder = WebApplication.CreateBuilder(args);

// Add service to the container
builder.Services.AddControllers();
builder.Services.AddDbContext<StoreContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("DefaultConnection"));
});
// Read-only card databases (kept separate per game).
builder.Services.AddDbContext<OnePieceContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("OnePieceConnection"));
});
builder.Services.AddDbContext<PokemonContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("PokemonConnection"));
});
// Read-only model predictions (produced offline by the ML pipeline).
builder.Services.AddDbContext<PredictionsContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("PredictionsConnection"));
});
// Read-only PriceCharting graded prices.
builder.Services.AddDbContext<PriceChartingContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("PriceChartingConnection"));
});
builder.Services.AddCors();
// builder.Services.AddOpenApi();
builder.Services.AddTransient<ExceptionMiddleware>();
builder.Services.AddIdentityApiEndpoints<User>(opt =>
{
    opt.User.RequireUniqueEmail  = true;
})
    .AddRoles<IdentityRole>()
    .AddEntityFrameworkStores<StoreContext>();

var app = builder.Build();

// Configure the HTTP request pipeline
app.UseMiddleware<ExceptionMiddleware>();
app.UseCors(opt =>
{
   opt.AllowAnyHeader().AllowAnyMethod().AllowCredentials().WithOrigins("https://localhost:5173") ;
});

// Serve card images straight from the scraper's image folders (no copy).
ServeCardImages(app, "CardImages:OnePiece", "/card-images/onepiece");
ServeCardImages(app, "CardImages:Pokemon", "/card-images/pokemon");

app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapGroup("api").MapIdentityApi<User>();

// Fallback for card images that aren't on disk (the static-file middleware above
// calls next() on a miss): redirect to the card's remote image instead of letting
// the browser get a 404 HTML page — which cross-origin trips Firefox's ORB.
app.MapGet("/card-images/{game}/{file}", async (string game, string file,
    OnePieceContext onePiece, PokemonContext pokemon) =>
{
    if (!int.TryParse(Path.GetFileNameWithoutExtension(file), out var id))
        return Results.NotFound();

    var remote = string.Equals(game, "pokemon", StringComparison.OrdinalIgnoreCase)
        ? (await pokemon.Cards.FindAsync(id))?.ImageUrl
        : (await onePiece.Cards.FindAsync(id))?.ImageUrl;

    return string.IsNullOrEmpty(remote) ? Results.NotFound() : Results.Redirect(remote);
});

await DbInitializer.InitDb(app);   // finish migrating/seeding before serving requests

app.Run();

void ServeCardImages(WebApplication webApp, string configKey, string requestPath)
{
    var path = webApp.Configuration[configKey];
    if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path)) return;

    webApp.UseStaticFiles(new StaticFileOptions
    {
        FileProvider = new PhysicalFileProvider(path),
        RequestPath = requestPath
    });
}
