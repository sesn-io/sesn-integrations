using System.Net;
using System.Reflection;
using System.Text.Json;
using Jellyfin.Database.Implementations.Entities;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Serialization;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using Sesn.Jellyfin;
using Sesn.Jellyfin.Configuration;
using Sesn.Jellyfin.Playback;
using Sesn.Jellyfin.Services;

// Actual plugin classes and SDK types. HTTP is intercepted in memory; all keys,
// viewers, application paths, and configuration files are disposable fixtures.
var tests = new (string Name, Func<Task> Run)[]
{
    ("disconnect blocks playback before the pairing worker runs", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => { config.DisconnectRequested = true; config.PairingStatus = "disconnecting"; });
        await fixture.Play();
        Check(fixture.Requests.Count == 0, "Disconnected playback left the server.");
        Check(!fixture.Plugin.Credentials.HasApiKey, "Old key remains available as an active delivery key.");
    }),
    ("revocation timeout leaves playback stopped during retries", async () =>
    {
        using var fixture = new Fixture();
        fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
        fixture.Update(config => { config.DisconnectRequested = true; config.PairingStatus = "disconnecting"; });
        await Tick(fixture.Pairing);
        await fixture.Play();
        await Tick(fixture.Pairing);
        Check(fixture.Requests.All(request => request.Path == "/api/v1/link/revoke"), "Playback escaped during revocation backoff.");
    }),
    ("replacement pairing stops the old credential immediately", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => { config.PairingRequested = true; config.PairingStatus = "starting"; });
        await fixture.Play();
        Check(fixture.Requests.Count == 0, "Old viewer playback escaped while pairing its replacement.");
    }),
    ("disabled delivery also suppresses viewer reports", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => config.Enabled = false);
        await Tick(fixture.Viewers);
        Check(fixture.Requests.Count == 0, "Viewer identities escaped while delivery was disabled.");
    }),
    ("a late old-key rejection cannot reject a replacement key", async () =>
    {
        using var fixture = new Fixture();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = (_, _) => { entered.TrySetResult(); return release.Task; };
        var playback = fixture.Play();
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => { config.ApiKey = "synthetic-replacement"; config.PairingStatus = "connected"; });
        release.SetResult(new HttpResponseMessage(HttpStatusCode.Unauthorized));
        await playback;
        Check(fixture.Plugin.Configuration.PairingStatus == "connected", "An old HTTP response rejected the new connection.");
    }),
    ("disconnect cancels an in-flight playback request", async () =>
    {
        using var fixture = new Fixture();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = async (_, token) =>
        {
            using var registration = token.Register(() => cancelled.TrySetResult());
            entered.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return new HttpResponseMessage(HttpStatusCode.OK);
        };
        var playback = fixture.Play();
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => config.DisconnectRequested = true);
        await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await playback.WaitAsync(TimeSpan.FromSeconds(5));
        await fixture.Play();
        Check(fixture.Requests.Count == 1, "A second playback request began after disconnect.");
    }),
    ("replacement revokes only the old key at its original origin", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => { config.ApiKey = "synthetic-replacement"; config.SesnBaseUrl = "https://replacement.invalid"; });
        await Tick(fixture.Pairing);
        Check(fixture.Requests.Count == 1 && fixture.Requests[0] == ("/api/v1/link/revoke", "synthetic-active", "sesn.invalid"), "Revocation lost the old key or destination.");
        Check(fixture.Plugin.Credentials.ApiKey == "synthetic-replacement", "Finishing an old revocation removed the new key.");
        await fixture.Play();
        Check(fixture.Requests.Last().Host == "replacement.invalid" && fixture.Requests.Last().Key == "synthetic-replacement", "New playback did not use the replacement connection.");
    }),
    ("retired keys and their backoff survive a plugin restart without reactivation", async () =>
    {
        using var fixture = new Fixture();
        fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
        fixture.Update(config => config.DisconnectRequested = true);
        await Tick(fixture.Pairing);
        var pending = fixture.Plugin.Credentials.NextRevocation!;
        fixture.ReloadPlugin();
        var restored = fixture.Plugin.Credentials.NextRevocation!;
        Check(restored.Id == pending.Id && restored.Failures == 1 && restored.NextAttemptAt == pending.NextAttemptAt, "The pending revocation or retry boundary was lost on restart.");
        Check(!fixture.Plugin.Credentials.HasApiKey, "Restart made the retired key active.");
        await fixture.Play(); await Tick(fixture.Viewers); await Tick(fixture.Pairing);
        Check(fixture.Requests.Count == 1, "Restart bypassed revocation backoff or allowed delivery.");
        fixture.Plugin.Credentials.DelayRevocation(restored.Id, restored.Failures, DateTimeOffset.MinValue);
        fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.NoContent));
        await Tick(fixture.Pairing);
        Check(fixture.Plugin.Credentials.NextRevocation is null && fixture.Plugin.Configuration.PairingStatus == "not_connected", "Eventual remote revocation did not complete local disconnect.");
    }),
    ("eight failed revocations leave a durable manual-cleanup warning", async () =>
    {
        using var fixture = new Fixture();
        fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
        fixture.Update(config => config.DisconnectRequested = true);
        for (var attempt = 0; attempt < 8; attempt++)
        {
            var pending = fixture.Plugin.Credentials.NextRevocation!;
            fixture.Plugin.Credentials.DelayRevocation(pending.Id, pending.Failures, DateTimeOffset.MinValue);
            await Tick(fixture.Pairing);
            await fixture.Play(); await Tick(fixture.Viewers);
        }
        Check(fixture.Requests.Count == 8 && fixture.Requests.All(request => request.Path == "/api/v1/link/revoke"), "Revocation exhaustion leaked another request or exceeded its bound.");
        Check(fixture.Plugin.Credentials.NextRevocation is null && !fixture.Plugin.Credentials.HasApiKey, "Exhausted revocation retained an active key.");
        Check(!string.IsNullOrWhiteSpace(fixture.Plugin.Configuration.RevocationWarning), "Remote revocation failure was presented as success.");
        fixture.ReloadPlugin();
        Check(!string.IsNullOrWhiteSpace(fixture.Plugin.Configuration.RevocationWarning), "Restart removed the manual-cleanup warning.");
    }),
    ("an already-revoked key completes disconnect for both 401 and 403", async () =>
    {
        foreach (var status in new[] { HttpStatusCode.Unauthorized, HttpStatusCode.Forbidden })
        {
            using var fixture = new Fixture();
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(status));
            fixture.Update(config => config.DisconnectRequested = true);
            await Tick(fixture.Pairing);
            Check(fixture.Plugin.Configuration.PairingStatus == "not_connected" && fixture.Plugin.Credentials.NextRevocation is null, "Already-revoked credentials stayed pending.");
        }
    }),
    ("late viewer responses cannot restore an old allow-list after replacement", async () =>
    {
        using var fixture = new Fixture();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = (_, _) => { entered.TrySetResult(); return release.Task; };
        var report = Tick(fixture.Viewers);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => { config.ApiKey = "synthetic-replacement"; config.Household = false; config.TrackedViewerIds = []; });
        release.SetResult(Json("{\"household\":true,\"tracked\":[\"old-viewer\"]}"));
        try { await report; } catch (OperationCanceledException) { }
        Check(!fixture.Plugin.Configuration.Household && fixture.Plugin.Configuration.TrackedViewerIds.Length == 0, "An old allow-list changed the replacement connection.");
    }),
    ("late authorized pairing results are revoked instead of reconnecting after disconnect", async () =>
    {
        using var fixture = new Fixture();
        await BeginPairing(fixture);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = (_, _) => { entered.TrySetResult(); return release.Task; };
        var poll = Tick(fixture.Pairing);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => config.DisconnectRequested = true);
        release.SetResult(Json("{\"status\":\"authorized\",\"api_key\":\"synthetic-late\"}"));
        await poll;
        Check(!fixture.Plugin.Credentials.HasApiKey && fixture.Plugin.Credentials.NextRevocation?.ApiKey == "synthetic-late", "Late approval became an active key or was never retired.");
        await fixture.Play(); await Tick(fixture.Viewers);
        Check(fixture.Requests.All(request => request.Path != "/api/v1/webhooks/jellyfin" && request.Path != "/api/v1/connections/viewers"), "Delivery restarted after disconnect.");
    }),
    ("a settings change during authorization cannot re-poll a retired pairing grant", async () =>
    {
        using var fixture = new Fixture();
        await BeginPairing(fixture);
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = (_, _) => { entered.TrySetResult(); return release.Task; };
        var poll = Tick(fixture.Pairing);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => config.Enabled = false);
        release.SetResult(Json("{\"status\":\"authorized\",\"api_key\":\"synthetic-late\"}"));
        await poll;
        Check(fixture.Plugin.Credentials.DeviceCode is null && fixture.Plugin.Configuration.PairingStatus == "expired", "A retired grant can still be polled and activated after its revocation.");
        Check(fixture.Plugin.Credentials.NextRevocation?.ApiKey == "synthetic-late", "The superseded authorized key was lost.");
    }),
    ("a settings change during pairing start does not leave the UI stuck starting", async () =>
    {
        using var fixture = new Fixture();
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        fixture.Respond = (request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/api/v1/link/revoke") return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NoContent));
            entered.TrySetResult(); return release.Task;
        };
        fixture.Update(config => config.PairingRequested = true);
        var start = Tick(fixture.Pairing);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        fixture.Update(config => config.Enabled = false);
        release.SetResult(Json("{\"device_code\":\"synthetic-device\",\"user_code_display\":\"AAAA-BBBB\",\"verification_url\":\"https://sesn.invalid/link\",\"expires_in\":300}"));
        await start;
        Check(fixture.Plugin.Configuration.PairingStatus == "error", "A discarded start response left pairing permanently busy.");
        Check(fixture.Plugin.Credentials.DeviceCode is null, "A stale pairing start became current.");
    }),
    ("repeated disconnect and a stale connected settings save cannot restore delivery", async () =>
    {
        using var fixture = new Fixture();
        var stale = JsonSerializer.Deserialize<PluginConfiguration>(JsonSerializer.Serialize(fixture.Plugin.Configuration))!;
        fixture.Update(config => config.DisconnectRequested = true);
        var originalId = fixture.Plugin.Credentials.NextRevocation!.Id;
        fixture.Update(config => config.DisconnectRequested = true);
        fixture.Plugin.UpdateConfiguration(stale);
        Check(fixture.Plugin.Credentials.NextRevocation!.Id == originalId, "Repeated disconnect duplicated or replaced revocation work.");
        Check(fixture.Plugin.Configuration.PairingStatus == "disconnecting", "A stale settings save canceled disconnect.");
        await fixture.Play(); await Tick(fixture.Viewers);
        Check(fixture.Requests.Count == 0, "A stale settings save restored delivery.");
        var publicConfig = JsonSerializer.Serialize(fixture.Plugin.Configuration);
        Check(!publicConfig.Contains("synthetic-active", StringComparison.Ordinal), "A retired credential appeared in public configuration.");
    }),
    ("several replacements drain independently without clearing the latest active key", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => { config.ApiKey = "synthetic-middle"; config.SesnBaseUrl = "https://middle.invalid"; });
        fixture.Update(config => { config.ApiKey = "synthetic-newest"; config.SesnBaseUrl = "https://newest.invalid"; });
        await Tick(fixture.Pairing); await Tick(fixture.Pairing);
        Check(fixture.Requests.Count == 2 && fixture.Requests[0].Host == "sesn.invalid" && fixture.Requests[1].Host == "middle.invalid", "Old credentials lost their independent origins.");
        Check(fixture.Plugin.Credentials.ApiKey == "synthetic-newest" && fixture.Plugin.Credentials.NextRevocation is null, "Old revocation work consumed the new key.");
    }),
    ("a failed pairing-start request can be retried and normal approval still connects", async () =>
    {
        using var fixture = new Fixture();
        fixture.Respond = (request, _) => request.RequestUri!.AbsolutePath == "/api/v1/link/new"
            ? Task.FromException<HttpResponseMessage>(new HttpRequestException("Synthetic timeout"))
            : Task.FromResult(new HttpResponseMessage(HttpStatusCode.NoContent));
        fixture.Update(config => config.PairingRequested = true);
        try { await Tick(fixture.Pairing); } catch (HttpRequestException) { }
        Check(fixture.Plugin.Configuration.PairingStatus == "error", "A failed pairing start left a permanent busy state.");
        await BeginPairing(fixture);
        fixture.Respond = (_, _) => Task.FromResult(Json("{\"status\":\"authorized\",\"api_key\":\"synthetic-authorized\"}"));
        await Tick(fixture.Pairing);
        Check(fixture.Plugin.Configuration.PairingStatus == "connected" && fixture.Plugin.Credentials.ApiKey == "synthetic-authorized", "Current pairing approval no longer connects.");
        await fixture.Play();
        Check(fixture.Requests.Last().Path == "/api/v1/webhooks/jellyfin" && fixture.Requests.Last().Key == "synthetic-authorized", "A valid newly paired connection could not deliver playback.");
    }),
    ("reposting the current key with whitespace does not retire it", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => config.ApiKey = "  synthetic-active  ");
        await Tick(fixture.Pairing);
        Check(fixture.Plugin.Credentials.NextRevocation is null && fixture.Plugin.Credentials.ApiKey == "synthetic-active", "Whitespace caused an active key to revoke itself.");
        Check(fixture.Requests.Count == 0, "Reposting an unchanged key issued a revocation.");
    }),
    ("a key pending revocation cannot be reactivated", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => config.DisconnectRequested = true);
        var rejected = false;
        try { fixture.Update(config => { config.DisconnectRequested = false; config.ApiKey = "synthetic-active"; }); }
        catch (InvalidOperationException) { rejected = true; }
        Check(rejected && !fixture.Plugin.Credentials.HasApiKey, "The pending old credential was promoted back to active.");
        await fixture.Play(); await Tick(fixture.Viewers);
        Check(fixture.Requests.Count == 0, "The retired credential resumed delivery.");
    }),
    ("changing destination without a new key retires the old connection", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => config.SesnBaseUrl = "https://unpaired.invalid");
        await fixture.Play(); await Tick(fixture.Viewers);
        Check(fixture.Requests.Count == 0 && !fixture.Plugin.Credentials.HasApiKey, "The old key was sent to an unpaired destination.");
        Check(fixture.Plugin.Configuration.PairingStatus == "not_connected", "Destination-only change claimed to remain connected.");
        await Tick(fixture.Pairing);
        Check(fixture.Requests.Count == 1 && fixture.Requests[0] == ("/api/v1/link/revoke", "synthetic-active", "sesn.invalid"), "Destination change did not revoke at the original origin.");
    }),
    ("reposting the old key cannot implicitly move it to a different destination", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => { config.SesnBaseUrl = "https://unpaired.invalid"; config.ApiKey = " synthetic-active "; });
        await fixture.Play(); await Tick(fixture.Viewers);
        Check(fixture.Requests.Count == 0 && !fixture.Plugin.Credentials.HasApiKey, "Reposting the same key moved it to the new origin.");
        Check(fixture.Plugin.Configuration.PairingStatus == "not_connected", "The old key was reused instead of requiring a fresh connection.");
        Check(fixture.Plugin.Credentials.NextRevocation?.BaseUrl == "https://sesn.invalid", "The retired credential lost its original origin.");
    }),
    ("equivalent destination spelling does not unnecessarily retire an active key", async () =>
    {
        using var fixture = new Fixture();
        fixture.Update(config => config.SesnBaseUrl = " https://SESN.invalid:443/ ");
        await Tick(fixture.Pairing);
        Check(fixture.Plugin.Credentials.ApiKey == "synthetic-active" && fixture.Plugin.Credentials.NextRevocation is null, "Equivalent origin spelling retired a valid connection.");
    }),
};
tests = tests.Concat(CompletionTests.Cases).Concat(ViewerPrivacyTests.Cases).Concat(SeriesIdentityTests.Cases).ToArray();
var failed = 0;
foreach (var (name, run) in tests)
{
    try { await run(); Console.WriteLine($"PASS {name}"); }
    catch (Exception exception) { failed++; Console.Error.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}"); }
}
Console.WriteLine($"{tests.Length - failed}/{tests.Length} lifecycle tests passed");
return failed == 0 ? 0 : 1;

static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK) { Content = new StringContent(body) };
static async Task BeginPairing(Fixture fixture)
{
    fixture.Respond = (request, _) => Task.FromResult(request.RequestUri!.AbsolutePath == "/api/v1/link/new"
        ? Json("{\"device_code\":\"synthetic-device\",\"user_code_display\":\"AAAA-BBBB\",\"verification_url\":\"https://sesn.invalid/link\",\"expires_in\":300}")
        : new HttpResponseMessage(HttpStatusCode.NoContent));
    fixture.Update(config => config.PairingRequested = true);
    await Tick(fixture.Pairing);
    Check(fixture.Plugin.Configuration.PairingStatus == "pending", "Pairing fixture did not reach pending.");
}
static void Check(bool value, string message) { if (!value) throw new Exception(message); }
static Task Tick(object service) => (Task)service.GetType().GetMethod("TickAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(service, [CancellationToken.None])!;

sealed class Fixture : IDisposable
{
    private readonly string _directory = Path.Combine(Path.GetTempPath(), "sesn-jellyfin-test-" + Guid.NewGuid().ToString("N"));
    private readonly HttpClient _http;
    public string PrivateDirectory => Path.Combine(_directory, "Sesn");
    public Plugin Plugin { get; private set; }
    public PairingService Pairing { get; }
    public ViewerSyncService Viewers { get; }
    public CompletionDeliveryService Completions { get; }
    public string ViewerId => _viewer.Id.ToString("N");
    public List<User> LocalUsers { get; } = [];
    public PlaybackEventSender Playback { get; }
    public List<(string Path, string? Key, string Host)> Requests { get; } = [];
    public Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> Respond { get; set; } = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"household\":false,\"tracked\":[]}") });
    private readonly User _viewer;
    public Fixture()
    {
        Directory.CreateDirectory(_directory);
        var paths = new Mock<IApplicationPaths>();
        paths.SetupGet(paths => paths.PluginConfigurationsPath).Returns(_directory);
        paths.SetupGet(paths => paths.PluginsPath).Returns(_directory);
        paths.SetupGet(paths => paths.ProgramDataPath).Returns(_directory);
        paths.SetupGet(paths => paths.DataPath).Returns(_directory);
        var serializer = new Mock<IXmlSerializer>();
        serializer.Setup(serializer => serializer.DeserializeFromFile(It.IsAny<Type>(), It.IsAny<string>())).Returns(() => new PluginConfiguration());
        Plugin = new Plugin(paths.Object, serializer.Object);
        _viewer = new User("Synthetic viewer", "synthetic-auth", "synthetic-reset");
        _viewer.Id = Guid.NewGuid(); _viewer.Username = "Synthetic viewer";
        Plugin.UpdateConfiguration(new PluginConfiguration { Enabled = true, ApiKey = "synthetic-active", PairingStatus = "connected", JellyfinUserId = _viewer.Id.ToString(), SesnBaseUrl = "https://sesn.invalid" });
        var host = new Mock<IServerApplicationHost>();
        host.SetupGet(host => host.SystemId).Returns("synthetic-server");
        host.SetupGet(host => host.FriendlyName).Returns("Synthetic server");
        host.SetupGet(host => host.ApplicationVersionString).Returns("12.1.0");
        LocalUsers.Add(_viewer);
        var users = new Mock<IUserManager>(); users.Setup(users => users.GetUsers()).Returns(() => LocalUsers);
        _http = new HttpClient(new Handler(async (request, token) =>
        {
            Requests.Add((request.RequestUri!.AbsolutePath, request.Headers.TryGetValues("X-Api-Key", out var values) ? values.Single() : null, request.RequestUri.Host));
            return await Respond(request, token);
        }));
        var client = new SesnApiClient(_http, NullLogger<SesnApiClient>.Instance);
        Pairing = new PairingService(client, host.Object, NullLogger<PairingService>.Instance);
        Viewers = new ViewerSyncService(client, host.Object, users.Object, NullLogger<ViewerSyncService>.Instance);
        Completions = new CompletionDeliveryService(client, NullLogger<CompletionDeliveryService>.Instance);
        Playback = new PlaybackEventSender(host.Object, client, new PlaybackThrottle(), NullLogger<PlaybackEventSender>.Instance);
    }
    public void ReloadPlugin()
    {
        var storedConfig = JsonSerializer.Serialize(Plugin.Configuration);
        var paths = new Mock<IApplicationPaths>();
        paths.SetupGet(paths => paths.PluginConfigurationsPath).Returns(_directory);
        paths.SetupGet(paths => paths.PluginsPath).Returns(_directory);
        paths.SetupGet(paths => paths.ProgramDataPath).Returns(_directory);
        paths.SetupGet(paths => paths.DataPath).Returns(_directory);
        var serializer = new Mock<IXmlSerializer>();
        serializer.Setup(serializer => serializer.DeserializeFromFile(It.IsAny<Type>(), It.IsAny<string>())).Returns(() => JsonSerializer.Deserialize<PluginConfiguration>(storedConfig)!);
        Plugin = new Plugin(paths.Object, serializer.Object);
    }
    public void Update(Action<PluginConfiguration> update)
    {
        var config = JsonSerializer.Deserialize<PluginConfiguration>(JsonSerializer.Serialize(Plugin.Configuration))!;
        update(config); Plugin.UpdateConfiguration(config);
    }
    public Task Play() => Playback.SendAsync("PlaybackStop", new PlaybackProgressEventArgs
    {
        Item = new Movie { Id = Guid.NewGuid(), Name = "Synthetic movie", RunTimeTicks = TimeSpan.FromMinutes(90).Ticks },
        Users = [_viewer],
        PlaySessionId = "synthetic-session",
    }, false);
    public Task PlayItem(BaseItem item) => Playback.SendAsync("PlaybackStart", new PlaybackProgressEventArgs
    { Item = item, Users = [_viewer], PlaySessionId = "synthetic-session" });
    public Task Complete(Guid? itemId = null, string? sessionId = "synthetic-session") => Playback.SendAsync("PlaybackStop", new PlaybackProgressEventArgs
    {
        Item = new Movie { Id = itemId ?? Guid.NewGuid(), Name = "Synthetic movie", RunTimeTicks = TimeSpan.FromMinutes(90).Ticks },
        Users = [_viewer], PlaySessionId = sessionId,
    }, true);
    public void Dispose() { _http.Dispose(); Pairing.Dispose(); Viewers.Dispose(); Completions.Dispose(); Directory.Delete(_directory, recursive: true); }
}
sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => respond(request, cancellationToken);
}
