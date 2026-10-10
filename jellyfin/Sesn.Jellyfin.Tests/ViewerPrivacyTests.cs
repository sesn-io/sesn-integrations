using System.Net;
using System.Reflection;
using System.Text.Json;
using Jellyfin.Database.Implementations.Entities;
using Sesn.Jellyfin.Services;

static class ViewerPrivacyTests
{
    public static (string Name, Func<Task> Run)[] Cases =>
    [
        ("solo discovery reports only the selected viewer identity", async () =>
        {
            using var fixture = MultipleViewers();
            var reports = CaptureReports(fixture);
            await Tick(fixture.Viewers);
            Check(reports.Single().SequenceEqual([fixture.ViewerId]), "Solo discovery uploaded other local viewer identities.");
        }),
        ("unselected solo discovery reports no local viewer identities", async () =>
        {
            using var fixture = MultipleViewers();
            fixture.Update(config => config.JellyfinUserId = null);
            var reports = CaptureReports(fixture);
            await Tick(fixture.Viewers);
            Check(reports.Single().Length == 0, "No selection was treated as permission to upload all local identities.");
        }),
        ("missing selected local viewer never falls back to other viewers", async () =>
        {
            using var fixture = MultipleViewers();
            fixture.Update(config => config.JellyfinUserId = Guid.NewGuid().ToString());
            var reports = CaptureReports(fixture);
            await Tick(fixture.Viewers);
            Check(reports.Single().Length == 0, "Missing selected viewer exposed another local identity.");
        }),
        ("only authenticated household mode enables full identity discovery", async () =>
        {
            using var fixture = MultipleViewers();
            fixture.Update(config => config.Household = true);
            var reports = CaptureReports(fixture, household: true);
            await Tick(fixture.Viewers);
            Check(reports.Single().SequenceEqual([fixture.ViewerId]), "A settings post enabled household discovery without an authenticated report.");
            fixture.Plugin.Configuration.ViewersSyncedAt = 0;
            await Tick(fixture.Viewers);
            Check(reports.Last().Length == fixture.LocalUsers.Count, "Confirmed household setup could not discover viewers for mapping.");
        }),
        ("authenticated household disable returns discovery to the selected viewer", async () =>
        {
            using var fixture = MultipleViewers();
            CaptureReports(fixture, household: true); await Tick(fixture.Viewers);
            fixture.Plugin.Configuration.ViewersSyncedAt = 0;
            var reports = CaptureReports(fixture, household: false);
            await Tick(fixture.Viewers);
            fixture.Plugin.Configuration.ViewersSyncedAt = 0;
            await Tick(fixture.Viewers);
            Check(reports.Last().SequenceEqual([fixture.ViewerId]), "Solo mode kept uploading household identities after the server disabled it.");
        }),
        ("expired household playback permissions stop live events but leave discovery refresh available", async () =>
        {
            using var fixture = MultipleViewers();
            CaptureReports(fixture, household: true); await Tick(fixture.Viewers);
            ExpirePermission(fixture);
            fixture.Requests.Clear();
            await fixture.Play(); await fixture.Complete();
            Check(fixture.Requests.Count == 0, "Expired household playback still left the server.");
            Check(!File.Exists(Path.Combine(fixture.PrivateDirectory, "completion-outbox.json")), "Expired permission captured newly observed household playback.");
            var reports = CaptureReports(fixture, household: true);
            await Tick(fixture.Viewers);
            Check(reports.Count == 1, "Expired playback permission also blocked permission refresh.");
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK));
            fixture.Requests.Clear(); await fixture.Play();
            Check(fixture.Requests.Count == 1, "Fresh authenticated permission did not resume playback.");
        }),
        ("failed household refresh and settings timestamps cannot extend permission", async () =>
        {
            using var fixture = MultipleViewers();
            CaptureReports(fixture, household: true); await Tick(fixture.Viewers);
            ExpirePermission(fixture);
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
            await Tick(fixture.Viewers);
            fixture.Plugin.Configuration.ViewersSyncedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 9999;
            fixture.Requests.Clear(); await fixture.Play();
            Check(fixture.Requests.Count == 0 && fixture.Plugin.Configuration.HouseholdPlaybackPaused, "An unsuccessful report or public timestamp extended playback permission.");
        }),
        ("solo playback is unaffected by the household permission lease", async () =>
        {
            using var fixture = MultipleViewers();
            CaptureReports(fixture); await Tick(fixture.Viewers);
            ExpirePermission(fixture);
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK));
            fixture.Requests.Clear(); await fixture.Play();
            Check(fixture.Requests.Count == 1, "Household expiry unexpectedly paused solo playback.");
        }),
        ("expired household permissions hold existing completions with their original ownership", async () =>
        {
            using var fixture = MultipleViewers();
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(JsonSerializer.Serialize(new { household = true, tracked = new[] { fixture.ViewerId }, durable_completion = 1,
                    credential_generation = new string('1', 32), mapping_generations = new Dictionary<string, string> { [fixture.ViewerId] = new string('2', 32) } })),
            });
            await Tick(fixture.Viewers); await fixture.Complete();
            var path = Path.Combine(fixture.PrivateDirectory, "completion-outbox.json");
            var original = File.ReadAllText(path);
            ExpirePermission(fixture); fixture.Requests.Clear();
            await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0 && fixture.Plugin.Configuration.HeldCompletions == 1, "Expired household permission allowed queued delivery.");
            Check(File.ReadAllText(path) == original, "Permission expiry reassigned or discarded the original completion.");
        }),
        ("paused and disconnected discovery uploads no selected or household identities", async () =>
        {
            using var fixture = MultipleViewers();
            CaptureReports(fixture, household: true); await Tick(fixture.Viewers);
            fixture.Update(config => config.Enabled = false);
            var reports = CaptureReports(fixture, household: true); await Tick(fixture.Viewers);
            Check(reports.Count == 0, "Paused household discovery left the server.");
            fixture.Update(config => { config.Enabled = true; config.DisconnectRequested = true; });
            await Tick(fixture.Viewers);
            Check(reports.Count == 0, "Disconnected household discovery left the server.");
        }),
    ];

    private static void ExpirePermission(Fixture fixture)
    {
        fixture.Plugin.Configuration.ViewersSyncedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds() - 361;
        // The production permission clock is private, not writable through settings.
        typeof(Sesn.Jellyfin.Plugin).GetField("_viewerPermissionConfirmedAt", BindingFlags.Instance | BindingFlags.NonPublic)
            ?.SetValue(fixture.Plugin, DateTimeOffset.UtcNow.ToUnixTimeSeconds() - 361);
    }
    private static Fixture MultipleViewers()
    {
        var fixture = new Fixture();
        fixture.LocalUsers.Add(new User("Other synthetic viewer", "synthetic-auth", "synthetic-reset") { Id = Guid.NewGuid() });
        return fixture;
    }
    private static List<string[]> CaptureReports(Fixture fixture, bool household = false)
    {
        var reports = new List<string[]>();
        fixture.Respond = async (request, _) =>
        {
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
            reports.Add(body.RootElement.GetProperty("viewers").EnumerateArray().Select(viewer => viewer.GetProperty("id").GetString()!).ToArray());
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(JsonSerializer.Serialize(new { household, tracked = household ? new[] { fixture.ViewerId } : Array.Empty<string>() })),
            };
        };
        return reports;
    }
    private static Task Tick(object service) => (Task)service.GetType().GetMethod("TickAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(service, [CancellationToken.None])!;
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
}
