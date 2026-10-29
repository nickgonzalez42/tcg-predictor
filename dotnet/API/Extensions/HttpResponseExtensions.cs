namespace API.Extensions;

public static class HttpResponseExtensions
{
    // Let the browser hold a response for a couple of minutes (2026-10-29).
    //
    // The home page preloads its three data calls from the HTML so they
    // overlap the bundle download. Chrome reuses the preloaded response;
    // Safari does not, because these carried no cache lifetime, so every
    // iPhone fetched all three twice. Most Reddit ad traffic is iPhones, so
    // the preload was costing them three extra requests instead of saving
    // time. With a lifetime the second request is a cache hit.
    //
    // "private" because every call here goes out with credentials: only the
    // visitor's own browser may store it, never a shared proxy. Two minutes
    // comfortably covers the preload-to-fetch gap and a reload or two, and
    // the underlying data only changes once a night.
    public static void CacheForVisitor(this HttpResponse response, int seconds = 120)
        => response.Headers.CacheControl = $"private, max-age={seconds}";
}
