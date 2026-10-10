using System.Net;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sesn.Jellyfin.Services;

static class CompletionTests
{
    private const string Generation = "11111111111111111111111111111111";
    private const string Mapping = "22222222222222222222222222222222";
    private const string Replacement = "33333333333333333333333333333333";
    public static (string Name, Func<Task> Run)[] Cases =>
    [
        ("completion is persisted even when Sesn is unavailable", async () =>
        {
            using var fixture = new Fixture();
            fixture.Respond = (_, _) => throw new HttpRequestException("synthetic offline transport");
            await fixture.Complete();
            Check(File.Exists(Path.Combine(fixture.PrivateDirectory, "completion-outbox.json")), "Completed watch disappeared without durable storage.");
            Check(fixture.Requests.Count == 0, "Unnegotiated completion fell back to legacy delivery.");
        }),
        ("completion without captured generation stays held after capability discovery", async () =>
        {
            using var fixture = new Fixture();
            await fixture.Complete();
            await Negotiate(fixture);
            fixture.Requests.Clear();
            await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0 && fixture.Plugin.Configuration.HeldCompletions == 1, "Unknown capture ownership was filled in from a later report.");
        }),
        ("real completion sender persists before HTTP and uses the strict endpoint", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture);
            var item = Guid.NewGuid();
            await fixture.Complete(item);
            fixture.Requests.Clear();
            var stored = ReadItems(fixture).Single();
            fixture.Respond = async (request, _) =>
            {
                Check(ReadItems(fixture).Length == 1, "HTTP began before the completion was persisted.");
                var body = await request.Content!.ReadAsStringAsync();
                Check(body == stored.GetProperty("Payload").GetString(), "Delivery reconstructed the captured body.");
                return Ack(JsonDocument.Parse(body).RootElement.GetProperty("event_id").GetString()!);
            };
            await Tick(fixture.Completions);
            Check(fixture.Requests.Single().Path == "/api/v1/webhooks/jellyfin/events", "Completion used a legacy route.");
            Check(ReadItems(fixture).Length == 0 && fixture.Plugin.Configuration.LastDeliveredAtUtc is not null, "A matching durable receipt did not finish delivery.");
            await fixture.Complete(item);
            Check(ReadItems(fixture).Length == 0, "Duplicate stop after acknowledgement created another completion.");
        }),
        ("duplicate callbacks preserve first captured identity timestamp and payload", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture);
            var item = Guid.NewGuid();
            await fixture.Complete(item);
            var original = ReadItems(fixture).Single().GetRawText();
            await fixture.Complete(item);
            Check(ReadItems(fixture).Single().GetRawText() == original, "Duplicate callback changed the first capture.");
            await fixture.Complete(item, "second-session");
            Check(ReadItems(fixture).Length == 2, "Distinct provider sessions were incorrectly collapsed.");
        }),
        ("restart retains exact payload and waits for fresh authenticated capabilities", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture);
            await fixture.Complete();
            var original = ReadItems(fixture).Single().GetProperty("Payload").GetString();
            fixture.ReloadPlugin();
            fixture.Requests.Clear();
            await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Restart reused unverified capability metadata.");
            await Negotiate(fixture);
            fixture.Respond = async (request, _) =>
            {
                Check(await request.Content!.ReadAsStringAsync() == original, "Restart altered original event time or identity.");
                return Ack(JsonDocument.Parse(original!).RootElement.GetProperty("event_id").GetString()!);
            };
            await Tick(fixture.Completions);
            Check(ReadItems(fixture).Length == 0, "Persisted completion did not resume with matching fresh permissions.");
        }),
        ("legacy success and 404 never acknowledge or fall back", async () =>
        {
            foreach (var (status, body) in new[] { (200, "{\"ok\":true}"), (200, "{\"ok\":false,\"reason\":\"unmatched\"}"), (404, "{}") })
            {
                using var fixture = new Fixture();
                await Negotiate(fixture);
                await fixture.Complete();
                fixture.Requests.Clear();
                fixture.Respond = (_, _) => Task.FromResult(Json(status, body));
                await Tick(fixture.Completions);
                Check(fixture.Requests.Single().Path.EndsWith("/events", StringComparison.Ordinal), "Unsupported server received a legacy fallback.");
                var saved = ReadItems(fixture).Single();
                Check(saved.GetProperty("Attempts").GetInt32() == 1 && saved.GetProperty("State").GetString() == "Pending", "Ambiguous reply discarded or permanently failed the watch.");
            }
        }),
        ("disconnect and replacement never replay old-owner completions", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            fixture.Update(config => config.DisconnectRequested = true);
            fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Disconnected outbox continued sending.");
            fixture.Update(config => { config.DisconnectRequested = false; config.ApiKey = "synthetic-new-owner"; });
            await Negotiate(fixture); fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0 && ReadItems(fixture).Length == 1, "Old outbox migrated to a new credential.");
        }),
        ("disabled delivery suppresses queued completions until re-enabled and revalidated", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            fixture.Update(config => config.Enabled = false);
            fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Disabled outbox sent a completion.");
            fixture.Update(config => config.Enabled = true);
            await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Re-enabled delivery skipped capability revalidation.");
            await Negotiate(fixture);
            fixture.Respond = async (request, _) => Ack(JsonDocument.Parse(await request.Content!.ReadAsStringAsync()).RootElement.GetProperty("event_id").GetString()!);
            await Tick(fixture.Completions);
            Check(ReadItems(fixture).Length == 0, "Same-owner resume lost the completed watch.");
        }),
        ("household mapping changes hold captured events instead of routing them again", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture, household: true); await fixture.Complete();
            await Negotiate(fixture, household: true, mapping: Replacement);
            fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0 && fixture.Plugin.Configuration.HeldCompletions == 1, "Completion followed a remapped household viewer.");
        }),
        ("credential generation and selected viewer changes hold captured events", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            await Negotiate(fixture, generation: Replacement);
            fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Old generation replayed after a credential reset.");
            fixture.Update(config => config.JellyfinUserId = Guid.NewGuid().ToString());
            await Negotiate(fixture); fixture.Requests.Clear(); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 0, "Old viewer replayed under a different selected viewer.");
        }),
        ("disconnect cancels in-flight completion but preserves its record", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            fixture.Respond = async (_, token) => { entered.SetResult(); await Task.Delay(Timeout.InfiniteTimeSpan, token); return Json(200, "{}"); };
            var delivery = Tick(fixture.Completions);
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
            fixture.Update(config => config.DisconnectRequested = true);
            await delivery.WaitAsync(TimeSpan.FromSeconds(5));
            Check(ReadItems(fixture).Length == 1, "Cancellation discarded an unconfirmed completion.");
        }),
        ("permanent strict failures stay visible without repeated sending", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            var id = ReadItems(fixture).Single().GetProperty("EventId").GetString()!;
            fixture.Respond = (_, _) => Task.FromResult(Json(422, $"{{\"ok\":false,\"protocol_version\":1,\"event_id\":\"{id}\",\"error\":\"unmatched\",\"retryable\":false}}"));
            fixture.Requests.Clear(); await Tick(fixture.Completions); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 1 && fixture.Plugin.Configuration.FailedCompletions == 1, "Permanent failure was lost or retried indefinitely.");
            Check(ReadItems(fixture).Single().GetProperty("State").GetString() == "Failed", "Permanent failure payload was discarded.");
        }),
        ("transport failure backs off and retains original capture", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            var original = ReadItems(fixture).Single().GetProperty("Payload").GetString();
            fixture.Respond = (_, _) => throw new HttpRequestException("synthetic transport failure");
            fixture.Requests.Clear(); await Tick(fixture.Completions); await Tick(fixture.Completions);
            var item = ReadItems(fixture).Single();
            Check(fixture.Requests.Count == 1 && item.GetProperty("Attempts").GetInt32() == 1, "Backoff allowed immediate repeat attempts.");
            Check(item.GetProperty("Payload").GetString() == original, "Retry changed original completion data.");
        }),
        ("rate-limited completion respects Retry-After without losing the event", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            fixture.Respond = (_, _) =>
            {
                var response = Json(429, "{\"ok\":false,\"retryable\":true}");
                response.Headers.RetryAfter = new System.Net.Http.Headers.RetryConditionHeaderValue(TimeSpan.FromSeconds(90));
                return Task.FromResult(response);
            };
            var before = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            fixture.Requests.Clear(); await Tick(fixture.Completions); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 1 && ReadItems(fixture).Single().GetProperty("NextAttemptAt").GetInt64() >= before + 90, "Rate limit was acknowledged or immediately retried.");
            Check(fixture.Plugin.Configuration.PendingCompletions == 1, "Rate-limited completion disappeared from status.");
        }),
        ("oversized responses and request timeouts retain the completion", async () =>
        {
            foreach (var timeout in new[] { false, true })
            {
                using var fixture = new Fixture();
                await Negotiate(fixture); await fixture.Complete();
                fixture.Respond = (_, _) => timeout ? throw new TaskCanceledException("synthetic HTTP timeout")
                    : Task.FromResult(Json(200, new string('x', 20_000)));
                await Tick(fixture.Completions);
                Check(ReadItems(fixture).Single().GetProperty("Attempts").GetInt32() == 1, "An oversized response or timeout lost the pending record.");
                Check(fixture.Plugin.Configuration.LastDeliveredAtUtc is null, "An ambiguous result advertised completed delivery.");
            }
        }),
        ("a stalled response body has a bounded timeout after headers arrive", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            fixture.Respond = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(new StalledStream()) });
            await Tick(fixture.Completions).WaitAsync(TimeSpan.FromSeconds(20));
            Check(ReadItems(fixture).Single().GetProperty("Attempts").GetInt32() == 1, "Stalled body neither timed out nor retained its completion.");
        }),
        ("an old completion rejection cannot reject a replacement connection", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource<HttpResponseMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
            fixture.Respond = (_, _) => { entered.SetResult(); return release.Task; };
            var delivery = Tick(fixture.Completions);
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
            fixture.Update(config => config.ApiKey = "synthetic-new-owner");
            release.SetResult(Json(401, "{}"));
            await delivery.WaitAsync(TimeSpan.FromSeconds(5));
            Check(fixture.Plugin.Configuration.PairingStatus == "connected" && fixture.Plugin.Credentials.ApiKey == "synthetic-new-owner", "Late old completion response rejected a fresh connection.");
            Check(ReadItems(fixture).Length == 1, "Late old failure lost the original completion.");
        }),
        ("HTTP authentication rejection pauses delivery without discarding history", async () =>
        {
            using var fixture = new Fixture();
            await Negotiate(fixture); await fixture.Complete();
            fixture.Respond = (_, _) => Task.FromResult(Json(403, "{}"));
            fixture.Requests.Clear(); await Tick(fixture.Completions); await Tick(fixture.Completions);
            Check(fixture.Requests.Count == 1 && fixture.Plugin.Configuration.PairingStatus == "rejected", "Rejected credential kept sending completions.");
            Check(ReadItems(fixture).Length == 1 && fixture.Plugin.Configuration.HeldCompletions == 1, "Authentication rejection discarded its pending watch.");
        }),
        ("partial stops and transient cards never enter completion storage", async () =>
        {
            using var fixture = new Fixture();
            await fixture.Play();
            Check(!File.Exists(Path.Combine(fixture.PrivateDirectory, "completion-outbox.json")), "An incomplete stop entered the completion outbox.");
            Check(fixture.Requests.Single().Path == "/api/v1/webhooks/jellyfin", "Incomplete stop used completion semantics.");
        }),
        ("classifier accepts only a matching explicit durable protocol receipt", () =>
        {
            const string id = "jf:event";
            var valid = "{\"ok\":true,\"protocol_version\":1,\"event_id\":\"jf:event\",\"durable\":true,\"state\":\"applied\"}";
            Check(CompletionResponse.Classify(200, valid, id).Disposition == CompletionDisposition.Accepted, "Valid receipt was not accepted.");
            foreach (var invalid in new[] { "", "null", "[]", "{", valid.Replace("jf:event", "jf:other"), valid.Replace("\"durable\":true", "\"durable\":false"), valid.Replace("\"ok\":true", "\"ok\":false"), valid.Replace("\"protocol_version\":1", "\"protocol_version\":2"), valid.Replace("applied", "received") })
                Check(CompletionResponse.Classify(200, invalid, id).Disposition == CompletionDisposition.Retry, "Unknown response was treated as delivered.");
            Check(CompletionResponse.Classify(503, valid, id).Disposition == CompletionDisposition.Retry, "Failure HTTP status was treated as delivered.");
            return Task.CompletedTask;
        }),
        ("classifier treats only known strict permanent failures as permanent", () =>
        {
            var valid = "{\"ok\":false,\"protocol_version\":1,\"event_id\":\"e\",\"retryable\":false,\"error\":\"mapping_changed\"}";
            Check(CompletionResponse.Classify(409, valid, "e").Disposition == CompletionDisposition.Failed, "Known permanent failure was not held.");
            foreach (var invalid in new[] { valid.Replace("mapping_changed", "unknown"), valid.Replace("false,\"error", "true,\"error"), valid.Replace("\"e\"", "\"other\"") })
                Check(CompletionResponse.Classify(409, invalid, "e").Disposition == CompletionDisposition.Retry, "Unconfirmed failure was discarded.");
            Check(CompletionResponse.Classify(200, valid, "e").Disposition == CompletionDisposition.Retry, "Legacy 200 ok:false was treated as permanent.");
            Check(CompletionResponse.Classify(429, "{}", "e", 80).RetryAfterSeconds == 80, "Retry-After was lost.");
            return Task.CompletedTask;
        }),
        ("outbox ownership fingerprint includes canonical destination and excludes raw credentials", () =>
        {
            using var fixture = new StoreFixture(); fixture.Enqueue();
            var file = File.ReadAllText(fixture.Store.FilePath);
            Check(!file.Contains("synthetic-private-key", StringComparison.Ordinal), "Outbox contains raw credential.");
            Check(CompletionOutbox.OwnerFingerprint("synthetic-private-key", " https://SESN.invalid:443/") == fixture.Auth.Owner, "Canonical destination spelling changed ownership.");
            Check(CompletionOutbox.OwnerFingerprint("synthetic-private-key", "https://elsewhere.invalid") != fixture.Auth.Owner, "Ownership omitted its destination.");
            if (!OperatingSystem.IsWindows()) Check((File.GetUnixFileMode(fixture.Store.FilePath) & (UnixFileMode.GroupRead | UnixFileMode.OtherRead)) == 0, "Outbox is readable by other local users.");
            return Task.CompletedTask;
        }),
        ("unknown and malformed queue files are preserved byte for byte", () =>
        {
            foreach (var body in new[] { "{", "{}", "{\"Version\":2,\"Items\":[],\"Acknowledged\":[]}", "{\"Version\":1,\"Items\":[{\"Payload\":\"legacy\"}],\"Acknowledged\":[]}" })
            {
                using var fixture = new StoreFixture();
                File.WriteAllText(fixture.Store.FilePath, body);
                var loaded = new CompletionOutbox(fixture.Directory, () => fixture.Now);
                Check(!loaded.IsReadable && loaded.NextDue(fixture.Auth) is null, "Unknown queue acquired current ownership.");
                try { loaded.Enqueue("event", fixture.Auth, "viewer", fixture.Payload, 10); throw new Exception("Unknown queue accepted a rewrite."); }
                catch (InvalidDataException) { }
                Check(File.ReadAllText(loaded.FilePath) == body, "Unknown queue was rewritten.");
            }
            return Task.CompletedTask;
        }),
        ("payload ownership mismatch makes persisted queue fail closed", () =>
        {
            using var fixture = new StoreFixture(); fixture.Enqueue();
            var json = JsonNode.Parse(File.ReadAllText(fixture.Store.FilePath))!;
            json["Items"]![0]!["CredentialGeneration"] = Replacement;
            var changed = json.ToJsonString(); File.WriteAllText(fixture.Store.FilePath, changed);
            var loaded = new CompletionOutbox(fixture.Directory, () => fixture.Now);
            Check(!loaded.IsReadable && File.ReadAllText(loaded.FilePath) == changed, "Stored routing provenance was reconstructed from inconsistent metadata.");
            return Task.CompletedTask;
        }),
        ("missing ownership fields and trailing newlines fail strict validation", () =>
        {
            Check(!CompletionOutbox.IsEventId("event\n") && !CompletionOutbox.IsGeneration(Generation + "\n"), "Strict identifiers accepted a trailing newline.");
            using var fixture = new StoreFixture(); fixture.Enqueue();
            var json = JsonNode.Parse(File.ReadAllText(fixture.Store.FilePath))!;
            json["Items"]![0]!.AsObject().Remove("Household");
            var changed = json.ToJsonString(); File.WriteAllText(fixture.Store.FilePath, changed);
            var loaded = new CompletionOutbox(fixture.Directory, () => fixture.Now);
            Check(!loaded.IsReadable && File.ReadAllText(loaded.FilePath) == changed, "Missing ownership metadata defaulted to current routing.");
            return Task.CompletedTask;
        }),
        ("bounded outbox refuses overflow and reports expired unconfirmed watches", () =>
        {
            using var fixture = new StoreFixture();
            for (var i = 0; i < CompletionOutbox.Capacity; i++) fixture.Enqueue("event:" + i);
            try { fixture.Enqueue("overflow"); throw new Exception("Full queue accepted overflow."); } catch (InvalidOperationException) { }
            Check(fixture.Store.Status(fixture.Auth).Pending == CompletionOutbox.Capacity, "Overflow removed an existing watch.");
            fixture.Now += CompletionOutbox.Retention + TimeSpan.FromSeconds(1);
            var status = fixture.Store.Status(fixture.Auth);
            Check(status.Pending == 0 && status.Expired == CompletionOutbox.Capacity && status.LastError!.Contains("expired", StringComparison.Ordinal), "Expired watches disappeared without visible accounting.");
            return Task.CompletedTask;
        }),
        ("retry delay and failed state survive restart with immutable timestamps", () =>
        {
            using var fixture = new StoreFixture(); fixture.Enqueue();
            fixture.Store.Retry("event", "safe retry", 60);
            var loaded = new CompletionOutbox(fixture.Directory, () => fixture.Now);
            Check(loaded.NextDue(fixture.Auth) is null, "Restart bypassed retry delay.");
            fixture.Now += TimeSpan.FromSeconds(60);
            var item = loaded.NextDue(fixture.Auth)!;
            Check(item.WatchedAt == 123 && item.Attempts == 1, "Restart lost original timestamp or attempts.");
            loaded.Fail("event", "safe permanent");
            var failed = new CompletionOutbox(fixture.Directory, () => fixture.Now);
            Check(failed.NextDue(fixture.Auth) is null && failed.Status(fixture.Auth).Failed == 1, "Restart replayed a permanent failure.");
            return Task.CompletedTask;
        }),
        ("failed atomic persistence never makes unpersisted data deliverable", () =>
        {
            using var fixture = new StoreFixture();
            Directory.CreateDirectory(fixture.Store.FilePath + ".tmp");
            try { fixture.Enqueue(); throw new Exception("Broken storage accepted a completion."); } catch (UnauthorizedAccessException) { } catch (IOException) { }
            Check(fixture.Store.NextDue(fixture.Auth) is null && !File.Exists(fixture.Store.FilePath), "Failed persistence left a sendable memory-only record.");
            return Task.CompletedTask;
        }),
    ];

    private static async Task Negotiate(Fixture fixture, bool household = false, string generation = Generation, string mapping = Mapping)
    {
        fixture.Plugin.Configuration.ViewersSyncedAt = 0;
        fixture.Respond = (_, _) => Task.FromResult(Json(200, JsonSerializer.Serialize(new
        {
            household, tracked = new[] { fixture.ViewerId }, durable_completion = 1, credential_generation = generation,
            mapping_generations = new Dictionary<string, string> { [fixture.ViewerId] = mapping },
        })));
        await Tick(fixture.Viewers);
    }
    private static JsonElement[] ReadItems(Fixture fixture)
    {
        using var json = JsonDocument.Parse(File.ReadAllText(Path.Combine(fixture.PrivateDirectory, "completion-outbox.json")));
        return json.RootElement.GetProperty("Items").EnumerateArray().Select(item => item.Clone()).ToArray();
    }
    private static HttpResponseMessage Ack(string id) => Json(200, JsonSerializer.Serialize(new { ok = true, protocol_version = 1, event_id = id, durable = true, state = "applied" }));
    private static HttpResponseMessage Json(int status, string body) => new((HttpStatusCode)status) { Content = new StringContent(body) };
    private static Task Tick(object service) => (Task)service.GetType().GetMethod("TickAsync", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(service, [CancellationToken.None])!;
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }

    private sealed class StalledStream : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return 0;
        }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    private sealed class StoreFixture : IDisposable
    {
        public string Directory { get; } = Path.Combine(Path.GetTempPath(), "sesn-outbox-test-" + Guid.NewGuid().ToString("N"));
        public DateTimeOffset Now { get; set; } = DateTimeOffset.FromUnixTimeSeconds(2_000_000_000);
        public CompletionOutbox Store { get; }
        public CompletionAuthorization Auth { get; } = new(CompletionOutbox.OwnerFingerprint("synthetic-private-key", "https://sesn.invalid"), 1, Generation, false, new Dictionary<string, string>(), ["viewer"]);
        public JsonElement Payload => JsonSerializer.SerializeToElement(new { NotificationType = "PlaybackStop", PlayedToCompletion = true, UserId = "viewer", Name = "Synthetic" });
        public StoreFixture() { System.IO.Directory.CreateDirectory(Directory); Store = new(Directory, () => Now); }
        public void Enqueue(string id = "event") => Store.Enqueue(id, Auth, "viewer", Payload, 123);
        public void Dispose() => System.IO.Directory.Delete(Directory, true);
    }
}
