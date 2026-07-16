namespace API.RequestHelpers;

public class PaginationMetadata
{
    public int TotalCount {get; set;}
    public int PageSize {get; set;}
    public int CurrentPage {get; set;}
    public int TotalPages {get; set;}

    public static PaginationMetadata For(int totalCount, int pageNumber, int pageSize) => new()
    {
        TotalCount = totalCount,
        PageSize = pageSize,
        CurrentPage = pageNumber,
        TotalPages = (int)Math.Ceiling(totalCount / (double)pageSize),
    };
}