using Anthropic;
using Anthropic.Models.Messages;
using API.Data;
using API.Entities;
using Microsoft.EntityFrameworkCore;

namespace API.Services;

// Turns the pipeline's computed forecast facts into one short plain-English "take"
// using Claude, generated on demand and cached in store.db. The LLM only rewords
// facts we computed — it never invents numbers or claims. Disabled (returns null)
// when no Anthropic key is configured, so the app runs fine without one.
public class ReasoningService(
    PredictionsContext predictions, StoreContext store,
    IConfiguration config, ILogger<ReasoningService> logger)
{
    private const string System =
        "You explain a trading-card price forecast to a casual collector. You are given a " +
        "model's computed facts for ONE card. Write 2-3 short, plain sentences summarizing the " +
        "outlook and why. Rules: use ONLY the facts provided — never invent numbers, causes, or " +
        "claims, and don't hedge with information you weren't given. This is a model estimate, not " +
        "financial or investment advice; don't tell the reader to buy, sell, or hold. No preamble — " +
        "start with the outlook. Refer to the card by name when given.";

    public async Task<string?> GetAsync(string game, int productId, string? name, string? set)
    {
        var facts = await predictions.Forecasts
            .Where(f => f.Game == game && f.ProductId == productId && f.Target == "ungraded")
            .Select(f => new { f.Horizon, f.BasePrice, f.ForecastPrice, f.Reason, f.ScoredAt })
            .ToListAsync();
        if (facts.Count == 0) return null;

        var scoredAt = facts.Max(f => f.ScoredAt) ?? "";
        var cached = await store.ReasonProses
            .FirstOrDefaultAsync(x => x.Game == game && x.ProductId == productId);
        if (cached != null && cached.ScoredAt == scoredAt)
            return cached.Prose;   // fresh cache hit

        var apiKey = config["Anthropic:ApiKey"] ?? Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY");
        if (string.IsNullOrWhiteSpace(apiKey))
            return cached?.Prose;   // feature not configured — serve stale if we have it, else null

        string? prose;
        try
        {
            prose = await GenerateAsync(apiKey, name, set,
                facts.OrderBy(f => f.Horizon)
                     .Select(f => (f.Horizon, f.BasePrice, f.ForecastPrice, f.Reason)));
        }
        catch (Exception e)
        {
            logger.LogWarning(e, "Reasoning generation failed for {Game}/{Id}", game, productId);
            return cached?.Prose;   // fall back to stale prose rather than erroring the page
        }
        if (string.IsNullOrWhiteSpace(prose)) return cached?.Prose;

        if (cached == null)
            store.ReasonProses.Add(new ReasonProse
            {
                Game = game, ProductId = productId, ScoredAt = scoredAt, Prose = prose,
            });
        else
        {
            cached.Prose = prose;
            cached.ScoredAt = scoredAt;
            cached.GeneratedAt = DateTime.UtcNow;
        }
        await store.SaveChangesAsync();
        return prose;
    }

    private async Task<string?> GenerateAsync(
        string apiKey, string? name, string? set,
        IEnumerable<(string Horizon, double Base, double Forecast, string? Reason)> facts)
    {
        var lines = facts.Select(f =>
            $"- {f.Horizon}: now {f.Base:0.00} -> forecast {f.Forecast:0.00}. {f.Reason}");
        var userText =
            $"Card: {name ?? "(unknown)"}{(string.IsNullOrEmpty(set) ? "" : $" ({set})")}\n" +
            $"Forecast facts (ungraded):\n{string.Join("\n", lines)}";

        var client = new AnthropicClient
        {
            ApiKey = apiKey,
            Timeout = TimeSpan.FromSeconds(30),
        };

        var response = await client.Messages.Create(new MessageCreateParams
        {
            Model = config["Anthropic:Model"] ?? "claude-opus-4-8",
            MaxTokens = 220,
            System = System,
            OutputConfig = new OutputConfig { Effort = Effort.Low },   // simple rewording
            Messages = [new() { Role = Role.User, Content = userText }],
        });

        return response.Content
            .Select(b => b.Value).OfType<TextBlock>()
            .Select(t => t.Text).FirstOrDefault();
    }
}
