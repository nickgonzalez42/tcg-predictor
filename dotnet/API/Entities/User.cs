using API.Entities;
using Microsoft.AspNetCore.Identity;

public class User : IdentityUser
{
    public int? AddressId {get;set;}
    public Address? Address {get;set;}
}