namespace API.Entities;

// Pokémon specific columns on top of the shared card fields.
public class PokemonCard : CardBase
{
    public string? Hp { get; set; }
    public string? Stage { get; set; }
    public string? EnergyType { get; set; }   // energy_type
    public string? Attack1 { get; set; }
    public string? Attack2 { get; set; }
    public string? Attack3 { get; set; }
    public string? Attack4 { get; set; }
    public string? Weakness { get; set; }
    public string? Resistance { get; set; }
    public string? RetreatCost { get; set; }  // retreat_cost
    public string? FlavorText { get; set; }   // flavor_text
}
