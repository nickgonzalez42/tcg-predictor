namespace API.Entities;

// One Piece specific columns on top of the shared card fields.
public class OnePieceCard : CardBase
{
    public string? Color { get; set; }
    public string? Subtypes { get; set; }
    public string? Life { get; set; }
    public string? Power { get; set; }
    public string? Cost { get; set; }
    public string? Counter { get; set; }
    public string? Attribute { get; set; }
}
