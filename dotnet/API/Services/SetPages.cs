using System.Text.RegularExpressions;

namespace API.Services;

// Which sets have a static price-guide page (/sets/{game}/{slug}).
// pipeline/set_pages.py writes those pages and ships a manifest of their
// paths with them; this reads it, so the app links a card to its set page
// only when the page exists. The manifest changes with each night's render,
// so it is re-read whenever its timestamp moves.
public partial class SetPages(IConfiguration config)
{
    private readonly string _path =
        config["SetPagesManifest"] ?? "/srv/tcg/static/sets/manifest.txt";
    private readonly object _gate = new();
    private DateTime _stamp = DateTime.MinValue;
    private HashSet<string> _paths = [];

    [GeneratedRegex("[^a-z0-9]+")]
    private static partial Regex NotSlug();

    // The same slug set_pages.py makes: lower case, every run of anything
    // else becomes one dash, 80 characters at most.
    public static string Slug(string name)
    {
        var s = NotSlug().Replace(name.ToLowerInvariant(), "-").Trim('-');
        if (s.Length > 80) s = s[..80];
        return s.Length == 0 ? "set" : s;
    }

    private HashSet<string> Paths()
    {
        DateTime stamp;
        try { stamp = File.GetLastWriteTimeUtc(_path); }
        catch (IOException) { return _paths; }
        catch (UnauthorizedAccessException) { return _paths; }
        lock (_gate)
        {
            if (stamp == _stamp) return _paths;
            try
            {
                _paths = File.ReadAllLines(_path).Where(l => l.StartsWith("/sets/")).ToHashSet();
                _stamp = stamp;
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return _paths;
        }
    }

    // The set's page, or null: no page (under three priced cards), or two
    // sets of the game share the slug (the generator gives the second one a
    // "-2" this side cannot attribute, so neither is linked).
    public string? PathFor(string game, string? setName)
    {
        if (string.IsNullOrWhiteSpace(setName)) return null;
        var paths = Paths();
        var page = $"/sets/{game}/{Slug(setName.Trim())}";
        return paths.Contains(page) && !paths.Contains(page + "-2") ? page : null;
    }
}
