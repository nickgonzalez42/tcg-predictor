using Microsoft.AspNetCore.Mvc;

namespace API.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class BaseApiController : ControllerBase
    {
        // Absolute URL of a card's image. Prod serves art from the S3 bucket
        // behind CloudFront (CardImages:BaseUrl); dev, where the scraped files
        // are local, falls back to this API's own /card-images route.
        protected string CardImageUrl(string folder, int id)
        {
            var baseUrl = HttpContext.RequestServices
                .GetRequiredService<IConfiguration>()["CardImages:BaseUrl"];
            return string.IsNullOrWhiteSpace(baseUrl)
                ? $"{Request.Scheme}://{Request.Host}/card-images/{folder}/{id}.jpg"
                : $"{baseUrl.TrimEnd('/')}/{folder}/{id}.jpg";
        }
    }
}
