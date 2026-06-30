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

DbInitializer.InitDb(app);

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
