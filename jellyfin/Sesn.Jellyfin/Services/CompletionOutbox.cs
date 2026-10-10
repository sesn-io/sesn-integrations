using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace Sesn.Jellyfin.Services;

/// <summary>
/// Private, bounded storage for completed watches only. Ownership and routing
/// generations are captured at enqueue and are never reassigned during replay.
/// Unknown file formats/ownership are preserved untouched and fail closed.
/// </summary>
public sealed class CompletionOutbox
{
    public const int Capacity = 500;
    public static readonly TimeSpan Retention = TimeSpan.FromDays(7);
    private const int MaxPayloadBytes = 8192;
    private const int MaxFileBytes = 8 * 1024 * 1024;
    private const int MaxAcknowledged = 2000;
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private static readonly Regex OwnerPattern = new("^[a-f0-9]{64}\\z", RegexOptions.CultureInvariant);
    private static readonly Regex GenerationPattern = new("^[a-f0-9]{32}\\z", RegexOptions.CultureInvariant);
    private static readonly Regex EventPattern = new("^[A-Za-z0-9._:-]{1,128}\\z", RegexOptions.CultureInvariant);
    private readonly object _gate = new();
    private readonly string _directory;
    private readonly string _path;
    private readonly Func<DateTimeOffset> _clock;
    private StoredOutbox _data = new() { Version = 1 };
    private readonly bool _unreadable;

    public CompletionOutbox(string directory, Func<DateTimeOffset>? clock = null)
    {
        _directory = directory;
        _path = Path.Combine(directory, "completion-outbox.json");
        _clock = clock ?? (() => DateTimeOffset.UtcNow);
        if (!File.Exists(_path)) return;
        try
        {
            if (new FileInfo(_path).Length > MaxFileBytes) throw new InvalidDataException();
            var json = File.ReadAllText(_path);
            using var document = JsonDocument.Parse(json);
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                !document.RootElement.TryGetProperty("Version", out _) ||
                !document.RootElement.TryGetProperty("Items", out _) ||
                !document.RootElement.TryGetProperty("Acknowledged", out _)) throw new InvalidDataException();
            var loaded = JsonSerializer.Deserialize<StoredOutbox>(json, JsonOptions);
            if (loaded is null || loaded.Version != 1 || loaded.Items is null || loaded.Acknowledged is null ||
                loaded.Expired < 0 || loaded.LastAcceptedAt < 0 ||
                (loaded.LastAcceptedOwner is not null && !OwnerPattern.IsMatch(loaded.LastAcceptedOwner)) ||
                loaded.Items.Count > Capacity || loaded.Acknowledged.Count > MaxAcknowledged ||
                loaded.Items.Any(item => !ValidStoredItem(item)) ||
                loaded.Items.Select(item => item.EventId).Distinct(StringComparer.Ordinal).Count() != loaded.Items.Count ||
                loaded.Acknowledged.Any(item => item is null || !EventPattern.IsMatch(item.EventId) || !OwnerPattern.IsMatch(item.Owner) || item.At < 0))
            {
                throw new InvalidDataException();
            }
            _data = loaded;
        }
        catch (Exception)
        {
            // Do not rewrite a queue whose schema or provenance is unknown.
            _unreadable = true;
        }
    }

    public string FilePath => _path;
    public bool IsReadable => !_unreadable;
    public static bool IsGeneration(string? value) => value is not null && GenerationPattern.IsMatch(value);
    public static bool IsEventId(string? value) => value is not null && EventPattern.IsMatch(value);

    /// <summary>Non-reversible local ownership marker; the outbox never stores a raw credential.</summary>
    public static string OwnerFingerprint(string apiKey, string baseUrl)
    {
        if (string.IsNullOrWhiteSpace(apiKey)) throw new ArgumentException("A delivery credential is required.");
        var url = string.IsNullOrWhiteSpace(baseUrl) ? SesnUrl.Default : baseUrl.Trim();
        var destination = new Uri(url.TrimEnd('/') + "/", UriKind.Absolute).AbsoluteUri;
        return Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(destination + "\0" + apiKey.Trim())));
    }

    /// <summary>Stable provider-session identity when available; otherwise a new occurrence ID.</summary>
    public static string EventId(string owner, string? serverId, string? sessionId, string viewerId, string? itemId)
    {
        if (!IsEventId(sessionId) || string.IsNullOrWhiteSpace(serverId) || string.IsNullOrWhiteSpace(itemId)) return Guid.NewGuid().ToString("N");
        return "jf:" + Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join('\0', owner, serverId, sessionId, viewerId, itemId))));
    }

    /// <summary>Persists the original event before it may be sent. Duplicate callbacks keep the first capture.</summary>
    public bool Enqueue(string eventId, CompletionAuthorization authorization, string viewerId, JsonElement payload, long watchedAt)
    {
        lock (_gate)
        {
            EnsureReadable();
            if (!IsEventId(eventId) || !OwnerPattern.IsMatch(authorization.Owner) || string.IsNullOrWhiteSpace(viewerId) ||
                viewerId.Length > 128 || watchedAt < 0 || payload.ValueKind != JsonValueKind.Object)
            {
                throw new ArgumentException("Completion metadata is invalid.");
            }
            Prune();
            if (_data.Items.Any(item => item.EventId == eventId) || _data.Acknowledged.Any(item => item.EventId == eventId && item.Owner == authorization.Owner)) return false;
            if (_data.Items.Count >= Capacity)
            {
                Commit(_data with { LastError = "Completion storage is full. Use Sync from your server to recover missing watches." });
                throw new InvalidOperationException("Completion storage is full.");
            }
            var credentialGeneration = IsGeneration(authorization.CredentialGeneration) ? authorization.CredentialGeneration : null;
            var mappingGeneration = authorization.MappingGenerations.GetValueOrDefault(viewerId);
            if (!IsGeneration(mappingGeneration)) mappingGeneration = null;
            var body = JsonNode.Parse(payload.GetRawText())!.AsObject();
            body["event_id"] = eventId;
            body["watched_at"] = watchedAt;
            body.Remove("credential_generation");
            body.Remove("mapping_generation");
            if (credentialGeneration is not null) body["credential_generation"] = credentialGeneration;
            if (authorization.Household && mappingGeneration is not null) body["mapping_generation"] = mappingGeneration;
            var json = body.ToJsonString();
            if (Encoding.UTF8.GetByteCount(json) > MaxPayloadBytes) throw new ArgumentException("Completion metadata is too large.");
            var item = new PendingCompletion
            {
                EventId = eventId, Owner = authorization.Owner, ViewerId = viewerId, Household = authorization.Household,
                CredentialGeneration = credentialGeneration, MappingGeneration = authorization.Household ? mappingGeneration : null,
                WatchedAt = watchedAt, CapturedAt = _clock().ToUnixTimeSeconds(), Payload = json,
            };
            Commit(_data with { Items = [.. _data.Items, item] });
            return true;
        }
    }

    public PendingCompletion? NextDue(CompletionAuthorization authorization)
    {
        lock (_gate)
        {
            if (_unreadable) return null;
            Prune();
            var now = _clock().ToUnixTimeSeconds();
            return _data.Items.FirstOrDefault(item => item.State == CompletionState.Pending && item.NextAttemptAt <= now && CanDeliver(item, authorization));
        }
    }

    public static bool CanDeliver(PendingCompletion item, CompletionAuthorization authorization) =>
        authorization.ProtocolVersion == 1 && item.Owner == authorization.Owner && item.Household == authorization.Household &&
        IsGeneration(item.CredentialGeneration) && item.CredentialGeneration == authorization.CredentialGeneration &&
        authorization.AllowedViewerIds.Contains(item.ViewerId, StringComparer.Ordinal) &&
        (!item.Household || (IsGeneration(item.MappingGeneration) &&
            authorization.MappingGenerations.GetValueOrDefault(item.ViewerId) == item.MappingGeneration));

    public void Retry(string eventId, string reason, int? retryAfterSeconds = null)
    {
        lock (_gate)
        {
            EnsureReadable();
            var item = _data.Items.Find(item => item.EventId == eventId);
            if (item is null) return;
            var attempts = Math.Min(item.Attempts + 1, 1000);
            var delay = retryAfterSeconds is > 0 ? Math.Clamp(retryAfterSeconds.Value, 5, 3600)
                : (int)Math.Min(1800, 5 * Math.Pow(2, Math.Min(attempts - 1, 9)));
            var updated = item with { Attempts = attempts, NextAttemptAt = _clock().ToUnixTimeSeconds() + delay, LastError = reason };
            Commit(_data with { Items = _data.Items.Select(value => value.EventId == eventId ? updated : value).ToList(), LastError = reason });
        }
    }

    /// <summary>Permanent failures stay visible, with their original ownership and payload retained.</summary>
    public void Fail(string eventId, string reason)
    {
        lock (_gate)
        {
            EnsureReadable();
            Commit(_data with
            {
                Items = _data.Items.Select(item => item.EventId == eventId ? item with { State = CompletionState.Failed, LastError = reason } : item).ToList(),
                LastError = reason,
            });
        }
    }

    public void Acknowledge(string eventId, string owner)
    {
        lock (_gate)
        {
            EnsureReadable();
            if (!_data.Items.Any(item => item.EventId == eventId && item.Owner == owner)) return;
            var now = _clock().ToUnixTimeSeconds();
            Commit(_data with
            {
                Items = _data.Items.Where(item => item.EventId != eventId || item.Owner != owner).ToList(),
                Acknowledged = _data.Acknowledged.Append(new AcknowledgedCompletion { EventId = eventId, Owner = owner, At = now }).TakeLast(MaxAcknowledged).ToList(),
                LastAcceptedAt = now, LastAcceptedOwner = owner, LastError = null,
            });
        }
    }

    public CompletionOutboxStatus Status(CompletionAuthorization? authorization = null)
    {
        lock (_gate)
        {
            if (_unreadable) return new(0, 0, 0, 0, null, "Stored completions have an unknown format or ownership. They are preserved and will not be replayed.");
            Prune();
            var pending = _data.Items.Count(item => item.State == CompletionState.Pending && authorization is not null && CanDeliver(item, authorization));
            var failed = _data.Items.Count(item => item.State == CompletionState.Failed);
            var held = _data.Items.Count - pending - failed;
            return new(pending, held, failed, _data.Expired, authorization?.Owner == _data.LastAcceptedOwner ? _data.LastAcceptedAt : null,
                held > 0 ? "Some completions lack current connection permission. They stay on this server; use Sync from your server to recover them."
                    : failed > 0 ? _data.Items.Last(item => item.State == CompletionState.Failed).LastError : _data.LastError);
        }
    }

    private void EnsureReadable()
    {
        if (_unreadable) throw new InvalidDataException("Unknown completion queue is preserved; it cannot be rewritten.");
    }

    private void Prune()
    {
        var cutoff = _clock().ToUnixTimeSeconds() - (long)Retention.TotalSeconds;
        var items = _data.Items.Where(item => item.CapturedAt >= cutoff).ToList();
        var acknowledged = _data.Acknowledged.Where(item => item.At >= cutoff).ToList();
        var expired = _data.Items.Count - items.Count;
        if (expired == 0 && acknowledged.Count == _data.Acknowledged.Count) return;
        Commit(_data with
        {
            Items = items, Acknowledged = acknowledged, Expired = _data.Expired + expired,
            LastError = expired > 0 ? "Some completions expired after seven days. Use Sync from your server to recover them." : _data.LastError,
        });
    }

    private static bool ValidStoredItem(PendingCompletion? item)
    {
        if (item is null || !IsEventId(item.EventId) || !OwnerPattern.IsMatch(item.Owner) ||
            string.IsNullOrWhiteSpace(item.ViewerId) || item.ViewerId.Length > 128 || item.WatchedAt < 0 || item.CapturedAt < 0 ||
            item.Attempts < 0 || item.NextAttemptAt < 0 || !Enum.IsDefined(item.State) ||
            (item.CredentialGeneration is not null && !IsGeneration(item.CredentialGeneration)) ||
            (item.MappingGeneration is not null && (!item.Household || !IsGeneration(item.MappingGeneration))) ||
            item.Payload is null || Encoding.UTF8.GetByteCount(item.Payload) > MaxPayloadBytes) return false;
        try
        {
            using var payload = JsonDocument.Parse(item.Payload);
            return payload.RootElement.GetProperty("event_id").GetString() == item.EventId &&
                payload.RootElement.GetProperty("watched_at").GetInt64() == item.WatchedAt &&
                ReadOptionalString(payload.RootElement, "credential_generation") == item.CredentialGeneration &&
                ReadOptionalString(payload.RootElement, "mapping_generation") == item.MappingGeneration &&
                ViewerSyncService.NormalizeId(payload.RootElement.GetProperty("UserId").GetString()!) == item.ViewerId;
        }
        catch (Exception) { return false; }
    }

    private static string? ReadOptionalString(JsonElement body, string name) =>
        body.TryGetProperty(name, out var value) ? value.GetString() : null;

    private void Commit(StoredOutbox next)
    {
        var json = JsonSerializer.Serialize(next, JsonOptions);
        if (Encoding.UTF8.GetByteCount(json) > MaxFileBytes) throw new InvalidDataException("Completion storage limit exceeded.");
        if (OperatingSystem.IsWindows()) Directory.CreateDirectory(_directory);
        else Directory.CreateDirectory(_directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        var temporary = _path + ".tmp";
        var options = new FileStreamOptions { Mode = FileMode.Create, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        using (var stream = new FileStream(temporary, options))
        {
            var bytes = Encoding.UTF8.GetBytes(json);
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(temporary, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        File.Move(temporary, _path, overwrite: true);
        _data = next;
    }

    private sealed record StoredOutbox
    {
        public int Version { get; init; }
        public List<PendingCompletion> Items { get; init; } = [];
        public List<AcknowledgedCompletion> Acknowledged { get; init; } = [];
        public int Expired { get; init; }
        public long? LastAcceptedAt { get; init; }
        public string? LastAcceptedOwner { get; init; }
        public string? LastError { get; init; }
    }
    private sealed record AcknowledgedCompletion
    {
        public string EventId { get; init; } = string.Empty;
        public string Owner { get; init; } = string.Empty;
        public long At { get; init; }
    }
}

public sealed record CompletionAuthorization(
    string Owner, int ProtocolVersion, string? CredentialGeneration, bool Household,
    IReadOnlyDictionary<string, string> MappingGenerations, IReadOnlyCollection<string> AllowedViewerIds);

[JsonConverter(typeof(JsonStringEnumConverter))]
public enum CompletionState { Pending, Failed }

public sealed record PendingCompletion
{
    [JsonRequired]
    public string EventId { get; init; } = string.Empty;
    [JsonRequired]
    public string Owner { get; init; } = string.Empty;
    [JsonRequired]
    public string ViewerId { get; init; } = string.Empty;
    [JsonRequired]
    public bool Household { get; init; }
    [JsonRequired]
    public string? CredentialGeneration { get; init; }
    [JsonRequired]
    public string? MappingGeneration { get; init; }
    [JsonRequired]
    public long WatchedAt { get; init; }
    [JsonRequired]
    public long CapturedAt { get; init; }
    [JsonRequired]
    public string Payload { get; init; } = string.Empty;
    [JsonRequired]
    public CompletionState State { get; init; }
    [JsonRequired]
    public int Attempts { get; init; }
    [JsonRequired]
    public long NextAttemptAt { get; init; }
    [JsonRequired]
    public string? LastError { get; init; }
}

public sealed record CompletionOutboxStatus(int Pending, int Held, int Failed, int Expired, long? LastAcceptedAt, string? LastError);
