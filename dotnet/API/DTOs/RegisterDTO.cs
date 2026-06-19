using System.ComponentModel.DataAnnotations;

namespace API.DTOS;

public class RegisterDto
{
    [Required]
    public string Email {get;set;} = string.Empty;
    public string Password {get;set;}
}