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
        var uri = BuildUri(config, "/api/v1/link/new");
        if (uri is null)
        {
            return null;
        }

        using var response = await _httpClient.PostAsJsonAsync(
            uri,
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
    public async Task<LinkPollResponse?> PollPairingAsync(PluginConfiguration config, string deviceCode, CancellationToken cancellationToken)
    {
        var uri = BuildUri(config, "/api/v1/link/poll");
        if (uri is null)
        {
            return null;
        }

        using var response = await _httpClient.PostAsJsonAsync(
            uri,
            new { device_code = deviceCode },
            JsonOptions,
            cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn pairing poll returned HTTP {StatusCode}", (int)response.StatusCode);
            return null;
        }

        return await response.Content.ReadFromJsonAsync<LinkPollResponse>(JsonOptions, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>
    /// Sends a playback event using the paired, revocable credential. Returns the HTTP
    /// status, or 0 when nothing was sent because the Sesn address is not allowed.
    /// </summary>
    public async Task<int> SendPlaybackAsync(PluginConfiguration config, string apiKey, object payload, CancellationToken cancellationToken)
    {
        var uri = BuildUri(config, "/api/v1/webhooks/jellyfin");
        if (uri is null)
        {
            return 0;
        }

        using var request = new HttpRequestMessage(HttpMethod.Post, uri)
        {
            Content = JsonContent.Create(payload, options: JsonOptions),
        };
        request.Headers.TryAddWithoutValidation("X-Api-Key", apiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn playback delivery returned HTTP {StatusCode}", (int)response.StatusCode);
        }

        return (int)response.StatusCode;
    }

    /// <summary>
    /// Reports this server's local users so the owner can map them on sesn.io, and
    /// returns which of them Sesn accepts playback for. Unlisted users never leave the server.
    /// The status is 0 when nothing was sent because the Sesn address is not allowed.
    /// </summary>
    public async Task<(ViewerReport? Report, int Status)> ReportViewersAsync(
        PluginConfiguration config, string apiKey, string serverId, string serverName, IEnumerable<(string Id, string Name)> viewers, CancellationToken cancellationToken)
    {
        var uri = BuildUri(config, "/api/v1/connections/viewers");
        if (uri is null)
        {
            return (null, 0);
        }

        using var request = new HttpRequestMessage(HttpMethod.Post, uri)
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
        request.Headers.TryAddWithoutValidation("X-Api-Key", apiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Sesn viewer report returned HTTP {StatusCode}", (int)response.StatusCode);
            return (null, (int)response.StatusCode);
        }

        var report = await response.Content.ReadFromJsonAsync<ViewerReport>(JsonOptions, cancellationToken).ConfigureAwait(false);
        return (report, (int)response.StatusCode);
    }

    /// <summary>
    /// Revokes the installation credential at Sesn. 401 and 403 mean Sesn no longer
    /// accepts the key (for example it was already revoked on sesn.io), so there is
    /// nothing left to cut and that counts as revoked.
    /// </summary>
    public async Task<bool> RevokeAsync(PluginConfiguration config, string? apiKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            return true;
        }

        var uri = BuildUri(config, "/api/v1/link/revoke");
        if (uri is null)
        {
            return false;
        }

        using var request = new HttpRequestMessage(HttpMethod.Post, uri);
        request.Headers.TryAddWithoutValidation("X-Api-Key", apiKey);
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var status = (int)response.StatusCode;
        if (response.IsSuccessStatusCode || status == 401 || status == 403)
        {
            return true;
        }

        _logger.LogWarning("Sesn credential revocation returned HTTP {StatusCode}", status);
        return false;
    }

    private Uri? BuildUri(PluginConfiguration config, string path)
    {
        var origin = SesnUrl.Resolve(config.SesnBaseUrl, _logger);
        return origin is null ? null : new Uri(origin, path.TrimStart('/'));
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
