using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sesn.Jellyfin.Services;

/// <summary>
/// Keeps the Sesn API key and the pending device code out of the plugin
/// configuration. Jellyfin serves the configuration to its settings page, so
/// secrets live in a separate file that only this process reads
/// (owner read/write only on Linux and macOS).
/// </summary>
public sealed class CredentialStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private readonly object _gate = new();
    private readonly string _directory;
    private readonly string _path;
    private StoredCredentials _data;

    /// <summary>Initializes the store, reading any credential already saved in <paramref name="directory"/>.</summary>
    public CredentialStore(string directory)
    {
        _directory = directory;
        _path = Path.Combine(directory, "credentials.json");
        _data = Load(_path);
    }

    /// <summary>Gets the full path of the credential file.</summary>
    public string FilePath => _path;

    /// <summary>Gets the paired installation key, or null.</summary>
    public string? ApiKey
    {
        get
        {
            lock (_gate)
            {
                return _data.ApiKey;
            }
        }
    }

    /// <summary>Gets the secret device code of a pending pairing, or null.</summary>
    public string? DeviceCode
    {
        get
        {
            lock (_gate)
            {
                return _data.DeviceCode;
            }
        }
    }

    /// <summary>Gets a value indicating whether an installation key is stored.</summary>
    public bool HasApiKey => !string.IsNullOrWhiteSpace(ApiKey);

    /// <summary>Replaces the installation key and the pending device code together.</summary>
    public void Set(string? apiKey, string? deviceCode)
    {
        lock (_gate)
        {
            _data = new StoredCredentials
            {
                ApiKey = string.IsNullOrWhiteSpace(apiKey) ? null : apiKey.Trim(),
                DeviceCode = string.IsNullOrWhiteSpace(deviceCode) ? null : deviceCode.Trim(),
                Revocations = _data.Revocations,
            };
            Save();
        }
    }

    /// <summary>Replaces only the pending device code.</summary>
    public void SetDeviceCode(string? deviceCode)
    {
        lock (_gate)
        {
            Set(_data.ApiKey, deviceCode);
        }
    }

    /// <summary>Atomically retires the active key into private, revocation-only storage.</summary>
    public void Retire(string baseUrl)
    {
        lock (_gate)
        {
            QueueRevocationLocked(_data.ApiKey, baseUrl);
            _data.ApiKey = null;
            _data.DeviceCode = null;
            Save();
        }
    }

    /// <summary>Queues a late pairing result without making it an active delivery key.</summary>
    public void QueueRevocation(string apiKey, string baseUrl)
    {
        lock (_gate)
        {
            QueueRevocationLocked(apiKey, baseUrl);
            Save();
        }
    }

    private void QueueRevocationLocked(string? apiKey, string baseUrl)
    {
        if (!string.IsNullOrWhiteSpace(apiKey) && !_data.Revocations.Any(item => item.ApiKey == apiKey && item.BaseUrl == baseUrl))
        {
            _data.Revocations.Add(new PendingRevocation { ApiKey = apiKey, BaseUrl = baseUrl });
        }
    }

    /// <summary>Prevents a pending revocation from being promoted back to an active key.</summary>
    public bool IsRetiredKey(string apiKey)
    {
        lock (_gate)
        {
            return _data.Revocations.Any(item => item.ApiKey == apiKey);
        }
    }

    /// <summary>Gets an isolated snapshot of the next private revocation work item.</summary>
    public PendingRevocation? NextRevocation
    {
        get
        {
            lock (_gate)
            {
                var next = _data.Revocations.FirstOrDefault();
                return next is null ? null : new PendingRevocation
                {
                    Id = next.Id, ApiKey = next.ApiKey, BaseUrl = next.BaseUrl,
                    Failures = next.Failures, NextAttemptAt = next.NextAttemptAt,
                };
            }
        }
    }

    /// <summary>Persists backoff so a restart cannot restart an exhausted retry cycle.</summary>
    public void DelayRevocation(Guid id, int failures, DateTimeOffset nextAttemptAt)
    {
        lock (_gate)
        {
            var pending = _data.Revocations.Find(item => item.Id == id);
            if (pending is null) return;
            pending.Failures = failures;
            pending.NextAttemptAt = nextAttemptAt;
            Save();
        }
    }

    /// <summary>Removes only the finished revocation, never a newer active key.</summary>
    public void CompleteRevocation(Guid id)
    {
        lock (_gate)
        {
            _data.Revocations.RemoveAll(item => item.Id == id);
            Save();
        }
    }

    /// <summary>Forgets active/pending-pairing secrets while preserving revocation work.</summary>
    public void Clear() => Set(null, null);

    private static StoredCredentials Load(string path)
    {
        try
        {
            return File.Exists(path)
                ? JsonSerializer.Deserialize<StoredCredentials>(File.ReadAllText(path), JsonOptions) ?? new StoredCredentials()
                : new StoredCredentials();
        }
        catch (Exception)
        {
            // A corrupt file means "not paired"; the administrator can pair again.
            return new StoredCredentials();
        }
    }

    private void Save()
    {
        if (OperatingSystem.IsWindows())
        {
            Directory.CreateDirectory(_directory);
        }
        else
        {
            Directory.CreateDirectory(_directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        }

        if (_data.ApiKey is null && _data.DeviceCode is null && _data.Revocations.Count == 0)
        {
            File.Delete(_path);
            return;
        }

        var temp = _path + ".tmp";
        var options = new FileStreamOptions { Mode = FileMode.Create, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows())
        {
            options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        }

        using (var stream = new FileStream(temp, options))
        {
            JsonSerializer.Serialize(stream, _data, JsonOptions);
        }

        if (!OperatingSystem.IsWindows())
        {
            File.SetUnixFileMode(temp, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }

        File.Move(temp, _path, overwrite: true);
    }

    private sealed class StoredCredentials
    {
        [JsonPropertyName("api_key")]
        public string? ApiKey { get; set; }

        [JsonPropertyName("device_code")]
        public string? DeviceCode { get; set; }

        [JsonPropertyName("revocations")]
        public List<PendingRevocation> Revocations { get; set; } = [];
    }
}

/// <summary>Private persisted work item; never exposed through plugin configuration or logs.</summary>
public sealed class PendingRevocation
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string ApiKey { get; set; } = string.Empty;
    public string BaseUrl { get; set; } = SesnUrl.Default;
    public int Failures { get; set; }
    public DateTimeOffset NextAttemptAt { get; set; } = DateTimeOffset.MinValue;
}
