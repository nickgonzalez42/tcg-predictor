using System.Threading.RateLimiting;
using Microsoft.EntityFrameworkCore;
using API.Data;
using API.Entities;
using API.Middleware;
using API.RequestHelpers;
using API.Services;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.FileProviders;

var builder = WebApplication.CreateBuilder(args);

// Add service to the container
builder.Services.AddControllers();
builder.Services.AddDbContext<StoreContext>(opt =>
{
    opt.UseSqlite(builder.Configuration.GetConnectionString("DefaultConnection"));
});
// Read-only card databases (kept separate per game). NoTracking: nothing is
// ever written back, so skip the change tracker on every query.
void AddReadOnlySqlite<T>(string connectionName) where T : DbContext =>
    builder.Services.AddDbContext<T>(opt => opt
        .UseSqlite(builder.Configuration.GetConnectionString(connectionName))
        .UseQueryTrackingBehavior(QueryTrackingBehavior.NoTracking));

AddReadOnlySqlite<OnePieceContext>("OnePieceConnection");
AddReadOnlySqlite<PokemonContext>("PokemonConnection");
AddReadOnlySqlite<YugiohContext>("YugiohConnection");
AddReadOnlySqlite<MagicContext>("MagicConnection");
AddReadOnlySqlite<LorcanaContext>("LorcanaConnection");
AddReadOnlySqlite<DigimonContext>("DigimonConnection");
AddReadOnlySqlite<GundamContext>("GundamConnection");
AddReadOnlySqlite<StarwarsContext>("StarwarsConnection");
builder.Services.AddScoped<CardSources>();
// Cross-database market context + the movers ranking built on it.
builder.Services.AddScoped<CardMarketData>();
builder.Services.AddScoped<MoverService>();
// Read-only model predictions (produced offline by the ML pipeline).
AddReadOnlySqlite<PredictionsContext>("PredictionsConnection");
// Read-only PriceCharting graded prices.
AddReadOnlySqlite<PriceChartingContext>("PriceChartingConnection");
builder.Services.AddScoped<ReasoningService>();
builder.Services.AddScoped<ModerationService>();
builder.Services.AddScoped<NotificationService>();
// Card-alert evaluation + opt-in email delivery (SES; fail-soft when unset).
builder.Services.AddScoped<AlertEvaluator>();
builder.Services.AddSingleton<EmailService>();
builder.Services.AddHostedService<AlertEmailNotifier>();
// S&P 500 closes for the portfolio benchmark (typed HttpClient, cache in store.db).
builder.Services.AddHttpClient<SpxService>();
builder.Services.AddCors();
// Response caches for the identical-for-everyone endpoints (movers, sitemaps).
builder.Services.AddMemoryCache();
// Per-IP fixed-window limits on the abuse-prone endpoints: LLM-backed
// reasoning, account creation, bulk CSV import, and multi-MB sitemap builds.
// RemoteIpAddress is the real client address here because UseForwardedHeaders
// rewrites it from Caddy's X-Forwarded-For before the limiter runs.
builder.Services.AddRateLimiter(opt =>
{
    opt.RejectionStatusCode = StatusCodes.Status429TooManyRequests;

    void PerIpPerMinute(string policy, int permits) => opt.AddPolicy(policy, ctx =>
        RateLimitPartition.GetFixedWindowLimiter(
            ctx.Connection.RemoteIpAddress?.ToString() ?? "unknown",
            _ => new FixedWindowRateLimiterOptions
            {
                PermitLimit = permits,
                Window = TimeSpan.FromMinutes(1),
            }));

    PerIpPerMinute("reasoning", 10);   // CardsController.GetReasoning (paid Anthropic calls)
    PerIpPerMinute("auth", 5);         // AccountController.RegisterUser
    PerIpPerMinute("import", 4);       // WatchlistController.ImportOwned (up to 1000 rows each)
    PerIpPerMinute("sitemap", 10);     // SitemapController (multi-MB XML on a cache miss)
});
// builder.Services.AddOpenApi();
builder.Services.AddTransient<ExceptionMiddleware>();
builder.Services.AddIdentityApiEndpoints<User>(opt =>
{
    opt.User.RequireUniqueEmail  = true;
})
    .AddRoles<IdentityRole>()
    .AddEntityFrameworkStores<StoreContext>();

// Google OAuth sign-in. Credentials come from configuration (user-secrets or
// appsettings.Development.json in dev, environment variables in prod) — the
// client secret is never committed. The external identity lands in Identity's
// external cookie; AccountController's callback turns it into a signed-in
// application cookie. CallbackPath sits under /api so Caddy proxies it in prod.
//
// Registered ONLY when credentials are present: the OAuth handler validates
// ClientId/ClientSecret every request (it's a request handler that checks its
// callback path), so registering it without them 500s every API call.
var googleId = builder.Configuration["Authentication:Google:ClientId"];
var googleSecret = builder.Configuration["Authentication:Google:ClientSecret"];
if (!string.IsNullOrWhiteSpace(googleId) && !string.IsNullOrWhiteSpace(googleSecret))
{
    builder.Services.AddAuthentication()
        .AddGoogle(options =>
        {
            options.ClientId = googleId;
            options.ClientSecret = googleSecret;
            options.CallbackPath = "/api/signin-google";
            options.SignInScheme = IdentityConstants.ExternalScheme;
        });
}

var app = builder.Build();

// Honor X-Forwarded-Proto/For from the Caddy reverse proxy (loopback, trusted
// by default) so Request.Scheme is https in prod. Without this the OAuth
// redirect URI would be built as http and rejected by Google.
app.UseForwardedHeaders(new ForwardedHeadersOptions
{
    ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto
});

// Configure the HTTP request pipeline
app.UseMiddleware<ExceptionMiddleware>();
app.UseCors(opt =>
{
   opt.AllowAnyHeader().AllowAnyMethod().AllowCredentials().WithOrigins("https://localhost:5173") ;
});

// After UseForwardedHeaders (so the partition key is the real client IP) and
// before auth — a rejected request shouldn't cost a cookie lookup. Only
// endpoints tagged [EnableRateLimiting] are limited; there is no global policy.
app.UseRateLimiter();

// Serve card images straight from the scraper's image folders (no copy).
foreach (var game in GameRegistry.Keys)
    ServeCardImages(app, $"CardImages:{game}", $"/card-images/{game}");

app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
// MapIdentityApi exposes the whole Identity surface (/register, /refresh,
// /forgotPassword, ...) but the SPA uses exactly one of its endpoints:
// POST /api/login?useCookies=true (the query string doesn't affect Path, so
// the filter passes it through). The rest are dead surface — and Identity's
// /api/register is worse than dead: it creates users WITHOUT the Member role,
// bypassing AccountController's /api/account/register. Everything but login
// answers 404, as if it were never mapped.
app.MapGroup("api").MapIdentityApi<User>()
    .AddEndpointFilter(async (ctx, next) =>
        ctx.HttpContext.Request.Path.Equals("/api/login", StringComparison.OrdinalIgnoreCase)
            ? await next(ctx)
            : Results.NotFound());

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
