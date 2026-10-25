using System.Numerics.Tensors;
using Microsoft.ML.OnnxRuntime;
using Microsoft.ML.OnnxRuntime.Tensors;
using SkiaSharp;

namespace API.Services;

// Search-by-photo: embed an uploaded image with the SAME CLIP encoder the
// pipeline embeds card art with (open_clip ViT-B-32/laion2b, exported to ONNX
// by pipeline/export_clip_onnx.py) and rank every card by cosine similarity.
//
// The uploaded image lives only in memory for the duration of the request —
// it is never written to disk or logged (the product promise is "temporarily,
// just for the search").
//
// Registered only when the artifacts exist (Data/clip/), same pattern as
// Google OAuth: a box without the files serves everything else unaffected.
public class ImageSearchService : IDisposable
{
    // Byte values in card-index.bin — must match export_clip_onnx.py GAME_ORDER
    // (append only; the byte is persisted in the index).
    private static readonly string[] GameOrder =
        ["pokemon", "magic", "yugioh", "onepiece", "lorcana", "digimon", "gundam", "starwars",
         "fleshandblood", "sorcery", "riftbound", "cyberpunk"];

    // CLIP's published preprocessing constants (bicubic 224 + center crop).
    private const int Side = 224;
    private static readonly float[] Mean = [0.48145466f, 0.4578275f, 0.40821073f];
    private static readonly float[] Std = [0.26862954f, 0.26130258f, 0.27577711f];

    private readonly InferenceSession _session;
    private readonly float[] _matrix;      // n × dim, L2-normalized rows (PCA space)
    private readonly (byte GameIdx, int ProductId)[] _keys;
    private readonly int _dim;             // index dimensions (256 by default)
    private readonly int _full;            // encoder dimensions (512)
    private readonly float[] _mean;        // PCA mean, length _full
    private readonly float[] _wt;          // projection, TRANSPOSED: dim × full rows

    public record Hit(string Game, int ProductId, float Score);

    public static bool ArtifactsExist(string dir) =>
        File.Exists(Path.Combine(dir, "clip-visual.onnx")) &&
        File.Exists(Path.Combine(dir, "card-index.bin"));

    // card-index.bin v2 (export_clip_onnx.py): b"CSI2", n, dim, full, mean[full],
    // W[full × dim] row-major, n × (byte game, int32 pid), n × dim float32.
    // The v1 layout (no magic, 512-d, no projection) still loads.
    public ImageSearchService(string dir)
    {
        _session = new InferenceSession(Path.Combine(dir, "clip-visual.onnx"));

        using var f = File.OpenRead(Path.Combine(dir, "card-index.bin"));
        using var r = new BinaryReader(f);
        var head = r.ReadBytes(4);
        int n;
        if (head.AsSpan().SequenceEqual("CSI2"u8))
        {
            n = r.ReadInt32();
            _dim = r.ReadInt32();
            _full = r.ReadInt32();
            _mean = ReadFloats(r, _full);
            var w = ReadFloats(r, (long)_full * _dim);          // W[f, d]
            _wt = new float[(long)_dim * _full];
            for (var fi = 0; fi < _full; fi++)
                for (var d = 0; d < _dim; d++)
                    _wt[(long)d * _full + fi] = w[(long)fi * _dim + d];
        }
        else
        {
            n = BitConverter.ToInt32(head);
            _dim = _full = r.ReadInt32();
            _mean = new float[_full];
            _wt = new float[(long)_dim * _full];
            for (var d = 0; d < _dim; d++) _wt[(long)d * _full + d] = 1f;
        }
        _keys = new (byte, int)[n];
        for (var i = 0; i < n; i++)
            _keys[i] = (r.ReadByte(), r.ReadInt32());
        _matrix = ReadFloats(r, (long)n * _dim);
    }

    private static float[] ReadFloats(BinaryReader r, long count)
    {
        var bytes = new byte[count * sizeof(float)];
        r.BaseStream.ReadExactly(bytes);
        var out_ = new float[count];
        Buffer.BlockCopy(bytes, 0, out_, 0, bytes.Length);
        return out_;
    }

    public int CardCount => _keys.Length;

    // Top-k cards for one uploaded image, optionally within one game.
    // Thread-safe: InferenceSession.Run is reentrant and the index is
    // read-only after construction. Returns null when the bytes don't decode
    // as an image.
    public List<Hit>? Search(Stream image, int k = 5, string? game = null)
    {
        var input = Preprocess(image);
        if (input == null) return null;
        using var results = _session.Run(
            [NamedOnnxValue.CreateFromTensor("image", input)]);
        var emb = results[0].AsEnumerable<float>().ToArray();   // L2-normalized by the model

        // Into the index's PCA space: q = normalize((emb - mean) · W).
        var centered = new float[_full];
        TensorPrimitives.Subtract(emb, _mean, centered);
        var q = new float[_dim];
        for (var d = 0; d < _dim; d++)
            q[d] = TensorPrimitives.Dot(_wt.AsSpan(d * _full, _full), centered);
        var norm = TensorPrimitives.Norm(q);
        if (norm > 0) TensorPrimitives.Divide(q, norm, q);

        // Cosine = dot product (both sides normalized). Full scan is ~60M
        // mults over 230k cards at 256-d — TensorPrimitives keeps it well
        // under 100ms. A game filter skips the other games' rows.
        var gameIdx = game == null ? -1 : Array.IndexOf(GameOrder, game);
        var n = _keys.Length;
        var scores = new float[n];
        for (var i = 0; i < n; i++)
            scores[i] = gameIdx >= 0 && _keys[i].GameIdx != gameIdx
                ? float.NegativeInfinity
                : TensorPrimitives.Dot(_matrix.AsSpan(i * _dim, _dim), q);

        return Enumerable.Range(0, n)
            .OrderByDescending(i => scores[i])
            .Take(k)
            .Where(i => !float.IsNegativeInfinity(scores[i]))
            .Select(i => new Hit(GameOrder[_keys[i].GameIdx], _keys[i].ProductId, scores[i]))
            .ToList();
    }

    // CLIP preprocessing: resize shortest side to 224 (cubic), center-crop
    // 224x224, scale to [0,1], normalize per channel, CHW layout. Null when
    // the stream isn't a decodable image.
    private static DenseTensor<float>? Preprocess(Stream stream)
    {
        using var original = SKBitmap.Decode(stream);
        if (original == null) return null;

        var scale = Side / (float)Math.Min(original.Width, original.Height);
        var w = (int)Math.Round(original.Width * scale);
        var h = (int)Math.Round(original.Height * scale);
        using var resized = original.Resize(new SKImageInfo(w, h),
            new SKSamplingOptions(SKCubicResampler.CatmullRom));
        if (resized == null) return null;

        var x0 = (w - Side) / 2;
        var y0 = (h - Side) / 2;
        var t = new DenseTensor<float>([1, 3, Side, Side]);
        for (var y = 0; y < Side; y++)
            for (var x = 0; x < Side; x++)
            {
                var p = resized.GetPixel(x0 + x, y0 + y);
                t[0, 0, y, x] = (p.Red / 255f - Mean[0]) / Std[0];
                t[0, 1, y, x] = (p.Green / 255f - Mean[1]) / Std[1];
                t[0, 2, y, x] = (p.Blue / 255f - Mean[2]) / Std[2];
            }
        return t;
    }

    public void Dispose() => _session.Dispose();
}

// DI wrapper so the app runs identically with or without the model artifacts
// on disk: Service is null when Data/clip/ is absent and the endpoint answers
// 503 instead of the whole container failing to resolve.
public sealed class ImageSearchHolder
{
    public ImageSearchService? Service { get; init; }
}
