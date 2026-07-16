using System.Security.Claims;
using API.DTOS;
using API.Entities;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace API.Controllers;

public class AccountController(SignInManager<User> signInManager, IConfiguration config) : BaseApiController
{
    [HttpPost("register")]
    public async Task<ActionResult> RegisterUser(RegisterDto registerDto)
    {
        var user = new User{UserName = registerDto.Email, Email = registerDto.Email};

        var result = await signInManager.UserManager.CreateAsync(user, registerDto.Password);

        if (!result.Succeeded)
        {
            foreach (var error in result.Errors)
            {
                ModelState.AddModelError(error.Code, error.Description);
            }

            return ValidationProblem();
        }

        await signInManager.UserManager.AddToRoleAsync(user, "Member");

        return Ok();
    }

    [HttpGet("user-info")]
    public async Task<ActionResult> GetUserInfo()
    {
        if (User.Identity.IsAuthenticated == false) return NoContent();

        var user = await signInManager.UserManager.GetUserAsync(User);

        if (user == null) return Unauthorized();

        var roles = await signInManager.UserManager.GetRolesAsync(user);

        return Ok(new
        {
            user.Email,
            user.UserName,
            Roles = roles
        });
    }

    [HttpPost("logout")]
    public async Task<ActionResult> Logout()
    {
        await signInManager.SignOutAsync();

        return NoContent();
    }

    // ----- External (Google) sign-in -----
    // The SPA opens /api/account/external-login in a full-page navigation. We
    // challenge the provider; it returns to the CallbackPath (/api/signin-google,
    // handled by the auth middleware), then here, where the external identity is
    // turned into a signed-in application cookie and the user is bounced back to
    // the app. OAuth is a redirect flow, so both endpoints are plain GETs.

    [HttpGet("external-login")]
    public IActionResult ExternalLogin(string provider = "Google")
    {
        // Provider only exists when its credentials are configured (see Program.cs);
        // degrade to a friendly login error rather than a 500 when it isn't.
        if (string.IsNullOrWhiteSpace(config["Authentication:Google:ClientId"]))
            return Redirect($"{config["ClientUrl"] ?? "https://localhost:5173"}/login?error=google-unconfigured");

        // Relative redirect back to us — the middleware keeps it on this host, so
        // it works behind the proxy without depending on the forwarded scheme.
        var redirectUrl = Url.Action(nameof(ExternalCallback));
        var props = signInManager.ConfigureExternalAuthenticationProperties(provider, redirectUrl);
        return Challenge(props, provider);
    }

    [HttpGet("external-callback")]
    public async Task<IActionResult> ExternalCallback()
    {
        var clientUrl = config["ClientUrl"] ?? "https://localhost:5173";
        var info = await signInManager.GetExternalLoginInfoAsync();
        if (info == null) return Redirect($"{clientUrl}/login?error=external");

        // Returning user: the external login is already linked to an account.
        var signIn = await signInManager.ExternalLoginSignInAsync(
            info.LoginProvider, info.ProviderKey, isPersistent: true, bypassTwoFactor: true);
        if (signIn.Succeeded) return Redirect($"{clientUrl}/portfolio");

        var email = info.Principal.FindFirstValue(ClaimTypes.Email);
        if (string.IsNullOrWhiteSpace(email)) return Redirect($"{clientUrl}/login?error=noemail");

        // First time in: link to an existing email account, or create a new one.
        // Handle stays null — set later in profile settings, same as email signups.
        var user = await signInManager.UserManager.FindByEmailAsync(email);
        if (user == null)
        {
            user = new User { UserName = email, Email = email, EmailConfirmed = true };
            var created = await signInManager.UserManager.CreateAsync(user);
            if (!created.Succeeded) return Redirect($"{clientUrl}/login?error=create");
            await signInManager.UserManager.AddToRoleAsync(user, "Member");
        }

        var linked = await signInManager.UserManager.AddLoginAsync(user, info);
        if (!linked.Succeeded) return Redirect($"{clientUrl}/login?error=link");

        await signInManager.SignInAsync(user, isPersistent: true);
        return Redirect($"{clientUrl}/portfolio");
    }

    [HttpPost("address")]
    public async Task<ActionResult<Address>> CreateOrUpdateAddress(Address address)
    {
        var user = await signInManager.UserManager.Users
            .Include(x => x.Address)
            .FirstOrDefaultAsync(x => x.UserName == User.Identity!.Name);

        if (user == null) return Unauthorized();

        user.Address = address;

        var result = await signInManager.UserManager.UpdateAsync(user);

        if(!result.Succeeded) return BadRequest("Problem updating user address");

        return Ok(user.Address);
    }

    [Authorize]
    [HttpGet("address")]
    public async Task<ActionResult<Address>> GetSavedAddress()
    {
        var address = await signInManager.UserManager.Users
            .Where(x => x.UserName == User.Identity!.Name)
            .Select(x => x.Address)
            .FirstOrDefaultAsync();

        if(address == null) return NoContent();

        return address;

    }
}