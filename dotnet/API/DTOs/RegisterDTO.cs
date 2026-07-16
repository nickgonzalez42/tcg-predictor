using System.ComponentModel.DataAnnotations;

namespace API.DTOS;

public class RegisterDto
{
    [Required]
    public string Email {get;set;} = string.Empty;
    [Required]
    public string Password {get;set;} = string.Empty;
}