using Amazon;
using Amazon.SimpleNotificationService;
using Amazon.SimpleNotificationService.Model;
using API.Entities;

namespace API.Services;

// Fires owner notifications (problem reports, new comments). Publishes to an
// SNS topic when one is configured (Aws:ReportsTopicArn) — subscribe your
// email to that topic to get notified. With no topic set it just logs, so the
// underlying record is stored either way. Credentials/region come from the EC2
// instance role and environment (no keys in config). Fail-soft: a notify
// failure never blocks the record from being saved.
public class NotificationService(IConfiguration config, ILogger<NotificationService> logger)
{
    private readonly string? _topicArn = config["Aws:ReportsTopicArn"];
    private readonly string? _region = config["Aws:Region"];
    private readonly string _siteUrl =
        (config["ClientUrl"] ?? "https://cardstock.guide").TrimEnd('/');

    public async Task NotifyProblemReport(ProblemReport report)
    {
        var body =
            $"New problem report on CardStock\n\n" +
            $"{report.Message}\n\n" +
            $"— Page:  {report.PageUrl ?? "-"}\n" +
            $"— From:  {report.Email ?? report.UserName ?? "anonymous"}\n" +
            $"— When:  {report.CreatedAt:u}\n" +
            $"— Id:    #{report.Id}";
        await Publish("CardStock — problem report", body, $"problem report #{report.Id}");
    }

    // Comment author is passed as the public handle — identity usernames
    // (emails) stay out of notification bodies too.
    public async Task NotifyComment(Comment comment, string handle)
    {
        var body =
            $"New comment on CardStock\n\n" +
            $"{handle} wrote:\n{comment.Body}\n\n" +
            $"— Card:  {_siteUrl}/catalog/{comment.Game}/{comment.ProductId}\n" +
            $"— Kind:  {(comment.ParentId is { } p ? $"reply to #{p}" : "top-level")}\n" +
            $"— When:  {comment.CreatedAt:u}\n" +
            $"— Id:    #{comment.Id}";
        await Publish("CardStock — new comment", body, $"comment #{comment.Id}");
    }

    private async Task Publish(string subject, string body, string context)
    {
        if (string.IsNullOrWhiteSpace(_topicArn))
        {
            logger.LogInformation("{Context} stored (no SNS topic configured)", context);
            return;
        }

        try
        {
            var cfg = new AmazonSimpleNotificationServiceConfig();
            if (!string.IsNullOrWhiteSpace(_region))
                cfg.RegionEndpoint = RegionEndpoint.GetBySystemName(_region);
            using var sns = new AmazonSimpleNotificationServiceClient(cfg);
            await sns.PublishAsync(new PublishRequest
            {
                TopicArn = _topicArn,
                Subject = subject,
                Message = body,
            });
        }
        catch (Exception ex)
        {
            // The record is already saved; the notification is best-effort.
            logger.LogWarning(ex, "Failed to publish {Context} to SNS", context);
        }
    }
}
