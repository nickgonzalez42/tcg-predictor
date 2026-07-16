using Amazon;
using Amazon.SimpleEmailV2;
using Amazon.SimpleEmailV2.Model;

namespace API.Services;

// Transactional email through AWS SES. The sender comes from config
// (Email:From, e.g. alerts@cardstock.guide) — unset means email is disabled
// and sends just log, so dev and a prod box without SES verified both degrade
// gracefully. Credentials/region come from the EC2 instance role and
// environment, mirroring NotificationService. Fail-soft by design.
public class EmailService(IConfiguration config, ILogger<EmailService> logger)
{
    private readonly string? _from = config["Email:From"];
    private readonly string? _region = config["Aws:Region"];

    public bool Enabled => !string.IsNullOrWhiteSpace(_from);

    public async Task<bool> SendAsync(string to, string subject, string body)
    {
        if (!Enabled)
        {
            logger.LogInformation("Email disabled (no Email:From) — would send \"{Subject}\" to {To}", subject, to);
            return false;
        }

        try
        {
            var cfg = new AmazonSimpleEmailServiceV2Config();
            if (!string.IsNullOrWhiteSpace(_region))
                cfg.RegionEndpoint = RegionEndpoint.GetBySystemName(_region);
            using var ses = new AmazonSimpleEmailServiceV2Client(cfg);

            await ses.SendEmailAsync(new SendEmailRequest
            {
                FromEmailAddress = _from,
                Destination = new Destination { ToAddresses = [to] },
                Content = new EmailContent
                {
                    Simple = new Message
                    {
                        Subject = new Content { Data = subject },
                        Body = new Body { Text = new Content { Data = body } },
                    },
                },
            });
            return true;
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "Failed to send email to {To}", to);
            return false;
        }
    }
}
