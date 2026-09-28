using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Sesn.Jellyfin.Configuration;

namespace Sesn.Jellyfin.Services;

/// <summary>Small, bounded client for Sesn pairing and playback delivery.</summary>
public sealed class SesnApiClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly HttpClient _httpClient;
    private readonly ILogger<SesnApiClient> _logger;

    /// <summary>Initializes the client.</summary>
    public SesnApiClient(HttpClient httpClient, ILogger<SesnApiClient> logger)
    {
        _httpClient = httpClient;
        _logger = logger;
    }

    /// <summary>Starts a passwordless device link.</summary>
    public async Task<LinkStartResponse?> StartPairingAsync(PluginConfiguration config, string serverName, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsJsonAsync(
            BuildUri(config, "/api/v1/link/new"),
            new { provider = "jellyfin", device_name = $"Jellyfin · {serverName}" },
            JsonOptions,
            cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn pairing start returned HTTP {StatusCode}", (int)response.StatusCode);
            return null;
        }

        return await response.Content.ReadFromJsonAsync<LinkStartResponse>(JsonOptions, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>Checks a pending device link.</summary>
    public async Task<LinkPollResponse?> PollPairingAsync(PluginConfiguration config, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsJsonAsync(
            BuildUri(config, "/api/v1/link/poll"),
            new { device_code = config.DeviceCode },
            JsonOptions,
            cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn pairing poll returned HTTP {StatusCode}", (int)response.StatusCode);
            return null;
        }

        return await response.Content.ReadFromJsonAsync<LinkPollResponse>(JsonOptions, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>Sends a playback event using the paired, revocable credential.</summary>
    public async Task<bool> SendPlaybackAsync(PluginConfiguration config, object payload, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, BuildUri(config, "/api/v1/webhooks/jellyfin"))
        {
            Content = JsonContent.Create(payload, options: JsonOptions),
        };
        request.Headers.TryAddWithoutValidation("X-Api-Key", config.ApiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn playback delivery returned HTTP {StatusCode}", (int)response.StatusCode);
            return false;
        }

        return true;
    }

    /// <summary>
    /// Reports this server's local users so the owner can map them on sesn.io, and
    /// returns which of them Sesn accepts playback for. Unlisted users never leave the server.
    /// </summary>
    public async Task<ViewerReport?> ReportViewersAsync(
        PluginConfiguration config, string serverId, string serverName, IEnumerable<(string Id, string Name)> viewers, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, BuildUri(config, "/api/v1/connections/viewers"))
        {
            Content = JsonContent.Create(
                new
                {
                    provider = "jellyfin",
                    server_id = serverId,
                    server_name = serverName,
                    viewers = viewers.Take(100).Select(viewer => new { id = viewer.Id, name = viewer.Name }),
                },
                options: JsonOptions),
        };
        request.Headers.TryAddWithoutValidation("X-Api-Key", config.ApiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn viewer report returned HTTP {StatusCode}", (int)response.StatusCode);
            return null;
        }

        return await response.Content.ReadFromJsonAsync<ViewerReport>(JsonOptions, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>Revokes the installation credential at Sesn.</summary>
    public async Task<bool> RevokeAsync(PluginConfiguration config, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(config.ApiKey))
        {
            return true;
        }

        using var request = new HttpRequestMessage(HttpMethod.Post, BuildUri(config, "/api/v1/link/revoke"));
        request.Headers.TryAddWithoutValidation("X-Api-Key", config.ApiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn credential revocation returned HTTP {StatusCode}", (int)response.StatusCode);
            return false;
        }

        return true;
    }

    private static Uri BuildUri(PluginConfiguration config, string path)
    {
        var origin = string.IsNullOrWhiteSpace(config.SesnBaseUrl) ? "https://sesn.io" : config.SesnBaseUrl.Trim();
        return new Uri(new Uri(origin.TrimEnd('/') + "/", UriKind.Absolute), path.TrimStart('/'));
    }
}

/// <summary>Response from the device-link start endpoint.</summary>
public sealed record LinkStartResponse(
    [property: System.Text.Json.Serialization.JsonPropertyName("user_code_display")] string UserCodeDisplay,
    [property: System.Text.Json.Serialization.JsonPropertyName("device_code")] string DeviceCode,
    [property: System.Text.Json.Serialization.JsonPropertyName("verification_url")] string VerificationUrl,
    [property: System.Text.Json.Serialization.JsonPropertyName("expires_in")] int ExpiresIn);

/// <summary>Response from the device-link poll endpoint.</summary>
public sealed record LinkPollResponse(
    string Status,
    [property: System.Text.Json.Serialization.JsonPropertyName("api_key")] string? ApiKey);

/// <summary>Response from the viewer report endpoint.</summary>
public sealed record ViewerReport(bool Household, string[]? Tracked);
