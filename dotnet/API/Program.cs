using Microsoft.EntityFrameworkCore;
using API.Data;
using API.Middleware;
using API.Services;
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
builder.Services.AddDbContext<YugiohContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("YugiohConnection"));
});
builder.Services.AddDbContext<MagicContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("MagicConnection"));
});
builder.Services.AddDbContext<LorcanaContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("LorcanaConnection"));
});
builder.Services.AddDbContext<DigimonContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("DigimonConnection"));
});
builder.Services.AddDbContext<GundamContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("GundamConnection"));
});
builder.Services.AddScoped<API.Services.CardSources>();
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
builder.Services.AddScoped<ReasoningService>();
builder.Services.AddScoped<API.Services.ModerationService>();
builder.Services.AddScoped<API.Services.NotificationService>();
// S&P 500 closes for the portfolio benchmark (typed HttpClient, cache in store.db).
builder.Services.AddHttpClient<API.Services.SpxService>();
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
foreach (var game in API.RequestHelpers.GameRegistry.Keys)
    ServeCardImages(app, $"CardImages:{game}", $"/card-images/{game}");

app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapGroup("api").MapIdentityApi<User>();

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
