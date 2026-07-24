using System.Net;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;

namespace API.Middleware;

public class ExceptionMiddleware(IHostEnvironment env, ILogger<ExceptionMiddleware> logger) : IMiddleware
{
    public async Task InvokeAsync(HttpContext context, RequestDelegate next)
    {
        try
        {
            await next(context);
        }
        catch (Exception ex)
        {
            await HandleException(context, ex);
        }
    }

    private async Task HandleException(HttpContext context, Exception ex)
    {
        logger.LogError(ex, ex.Message);
        context.Response.ContentType = "application/json";
        context.Response.StatusCode = (int)HttpStatusCode.InternalServerError;

        // Exception messages can name file paths and table/constraint names, so
        // outside Development the client gets a generic title (full details are
        // in the server log above).
        var response = new ProblemDetails
        {
            Status = 500,
            Detail = env.IsDevelopment() ? ex.StackTrace : null,
            Title = env.IsDevelopment() ? ex.Message : "An unexpected error occurred."
        };

        var options = new JsonSerializerOptions
            {PropertyNamingPolicy = JsonNamingPolicy.CamelCase};

        var json = JsonSerializer.Serialize(response, options);

        await context.Response.WriteAsync(json);
    }
}