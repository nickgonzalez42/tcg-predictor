using Anthropic;
using Anthropic.Models.Messages;

namespace API.Services;

// Comment automod, two layers:
//   1. Local heuristics (always on): length, link-spam, shout-spam, and a
//      slur/abuse blocklist — instant rejects, no network.
//   2. Claude (when an Anthropic key is configured): classifies anything the
//      heuristics pass as ALLOW / REMOVE. Fails OPEN — an API error lets the
//      comment through, since layer 1 already screened it.
public class ModerationService(IConfiguration config, ILogger<ModerationService> logger)
{
    public record Verdict(bool Allowed, string? Reason);

    private static readonly string[] Blocklist =
    [
        // intentionally short: the obvious abuse the LLM shouldn't even see
        "kys", "kill yourself", "nigger", "faggot", "retard",
    ];

    public async Task<Verdict> Check(string body)
    {
        var text = body.Trim();
        if (text.Length < 2) return new(false, "Comment is too short.");
        if (text.Length > 2000) return new(false, "Comment is too long (2000 characters max).");

        var lower = text.ToLowerInvariant();
        if (Blocklist.Any(lower.Contains))
            return new(false, "Comment contains abusive language.");

        var links = System.Text.RegularExpressions.Regex.Matches(lower, @"https?://").Count;
        if (links > 2)
            return new(false, "Too many links (2 max).");

        var letters = text.Count(char.IsLetter);
        if (letters > 40 && text.Count(char.IsUpper) > letters * 0.8)
            return new(false, "Please don't post in all caps.");

        return await ClaudeCheck(text);
    }

    private async Task<Verdict> ClaudeCheck(string text)
    {
        var apiKey = config["Anthropic:ApiKey"] ?? Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY");
        if (string.IsNullOrWhiteSpace(apiKey)) return new(true, null);

        try
        {
            var client = new AnthropicClient { ApiKey = apiKey, Timeout = TimeSpan.FromSeconds(10) };
            var response = await client.Messages.Create(new MessageCreateParams
            {
                // Override with Anthropic:ModerationModel (e.g. claude-haiku-4-5)
                // if automod cost/latency matters more than judgment quality.
                Model = config["Anthropic:ModerationModel"] ?? config["Anthropic:Model"] ?? "claude-opus-4-8",
                MaxTokens = 10,
                System = "You moderate comments on a trading-card price site. Reply with exactly " +
                         "ALLOW or REMOVE. REMOVE only for: harassment or personal attacks, hate " +
                         "speech, sexual content, doxxing, scam/phishing attempts, or spam " +
                         "(advertising unrelated to trading cards). Card talk, criticism of prices " +
                         "or the site, slang, and mild profanity are all ALLOW.",
                Messages = [new() { Role = Role.User, Content = text }],
            });

            var verdict = response.Content
                .Select(b => b.Value).OfType<TextBlock>()
                .Select(t => t.Text.Trim().ToUpperInvariant()).FirstOrDefault() ?? "ALLOW";
            return verdict.StartsWith("REMOVE")
                ? new(false, "Removed by the auto-moderator.")
                : new(true, null);
        }
        catch (Exception e)
        {
            // Fail open: heuristics already passed, and comments shouldn't 500
            // because the moderation API hiccuped.
            logger.LogWarning(e, "automod Claude check failed — allowing comment");
            return new(true, null);
        }
    }
}
