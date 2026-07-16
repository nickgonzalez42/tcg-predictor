namespace API.DTOS;

// Bulk owned-copy import (POST /watchlist/owned/import). Each row identifies a
// card by explicit ProductId or by Name (resolved server-side), and may carry a
// grade, quantity, price paid, and acquired date. Rows whose name matches more
// than one card come back as "ambiguous" with candidates for the user to pick.
public class OwnedImportDto
{
    public List<OwnedImportRow> Rows { get; set; } = new();
}

public class OwnedImportRow
{
    public string? Game { get; set; }
    public int? ProductId { get; set; }        // takes priority; null => resolve by Name
    public string? Name { get; set; }          // used when ProductId is null
    public string? Grade { get; set; }         // condition/copy vocab; blank = unspecified
    public int Quantity { get; set; } = 1;
    public double? PurchasePrice { get; set; }  // null => auto price (market on the acquired date)
    public string? AcquiredAt { get; set; }     // yyyy-MM-dd; null/blank => today
}

public class OwnedImportResult
{
    public int Added { get; set; }                              // total copies created
    public List<OwnedImportRowResult> Rows { get; set; } = new();
}

public class OwnedImportRowResult
{
    public int Index { get; set; }                 // matches the request row order
    public string Status { get; set; } = "error";  // imported | ambiguous | error
    public int Added { get; set; }
    public string? Message { get; set; }
    public List<ImportCandidate>? Candidates { get; set; }
}

public class ImportCandidate
{
    public required string Game { get; set; }
    public int ProductId { get; set; }
    public string? Name { get; set; }
    public string? SetName { get; set; }
    public string? Rarity { get; set; }
    public double? Price { get; set; }
    public string? ImageUrl { get; set; }
}
