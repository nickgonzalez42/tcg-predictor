using Microsoft.AspNetCore.Mvc;

namespace API.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class BaseApiController : ControllerBase
    {
        // Absolute URL of a card's locally-scraped image (served by this API).
        protected string CardImageUrl(string folder, int id) =>
            $"{Request.Scheme}://{Request.Host}/card-images/{folder}/{id}.jpg";
    }
}
