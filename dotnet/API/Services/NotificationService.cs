using Amazon;
using Amazon.SimpleNotificationService;
using Amazon.SimpleNotificationService.Model;
using API.Entities;

namespace API.Services;

// Fires a notification when a problem report is filed. Publishes to an SNS
// topic when one is configured (Aws:ReportsTopicArn) — subscribe your email to
// that topic to get notified. With no topic set it just logs, so the report is
// still stored either way. Credentials/region come from the EC2 instance role
// and environment (no keys in config). Fail-soft: a notify failure never blocks
// the report from being saved.
public class NotificationService(IConfiguration config, ILogger<NotificationService> logger)
{
    private readonly string? _topicArn = config["Aws:ReportsTopicArn"];
    private readonly string? _region = config["Aws:Region"];

    public async Task NotifyProblemReport(ProblemReport report)
    {
        if (string.IsNullOrWhiteSpace(_topicArn))
        {
            logger.LogInformation("Problem report #{Id} stored (no SNS topic configured)", report.Id);
            return;
        }

        try
        {
            var cfg = new AmazonSimpleNotificationServiceConfig();
            if (!string.IsNullOrWhiteSpace(_region))
                cfg.RegionEndpoint = RegionEndpoint.GetBySystemName(_region);
            using var sns = new AmazonSimpleNotificationServiceClient(cfg);

            var body =
                $"New problem report on TCG Predictor\n\n" +
                $"{report.Message}\n\n" +
                $"— Page:  {report.PageUrl ?? "-"}\n" +
                $"— From:  {report.Email ?? report.UserName ?? "anonymous"}\n" +
                $"— When:  {report.CreatedAt:u}\n" +
                $"— Id:    #{report.Id}";

            await sns.PublishAsync(new PublishRequest
            {
                TopicArn = _topicArn,
                Subject = "TCG Predictor — problem report",
                Message = body,
            });
        }
        catch (Exception ex)
        {
            // Report is already saved; the notification is best-effort.
            logger.LogWarning(ex, "Failed to publish problem report #{Id} to SNS", report.Id);
        }
    }
}
