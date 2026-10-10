using System.Text.Json;

namespace Sesn.Jellyfin.Services;

public enum CompletionDisposition { Retry, Accepted, Failed }

/// <summary>Strict, bounded completion protocol. Unknown replies retain the original watch.</summary>
public sealed record CompletionResponse(CompletionDisposition Disposition, int Status, string? Message = null, int? RetryAfterSeconds = null)
{
    public static CompletionResponse Classify(int status, string body, string eventId, int? retryAfterSeconds = null)
    {
        try
        {
            using var json = JsonDocument.Parse(body);
            var root = json.RootElement;
            if (root.GetProperty("protocol_version").GetInt32() == 1 && root.GetProperty("event_id").GetString() == eventId)
            {
                if (status is >= 200 and < 300 && root.GetProperty("ok").ValueKind == JsonValueKind.True &&
                    root.GetProperty("durable").ValueKind == JsonValueKind.True &&
                    root.GetProperty("state").GetString() is "applied" or "queued")
                    return new(CompletionDisposition.Accepted, status);
                if (status is >= 400 and < 500 && root.GetProperty("ok").ValueKind == JsonValueKind.False &&
                    root.GetProperty("retryable").ValueKind == JsonValueKind.False)
                {
                    var error = root.GetProperty("error").GetString();
                    var message = (status, error) switch
                    {
                        (400, "invalid_event" or "invalid_media" or "invalid_generation") => "Sesn could not accept a completion's metadata. Use Sync from your server to recover it.",
                        (409, "event_conflict") => "A completion identity conflicts with an earlier delivery. It is kept here for review.",
                        (409, "credential_changed" or "mapping_changed") => "Connection or viewer permission changed. The original completion is held here.",
                        (422, "unmatched") => "Sesn could not match a completed title. Check its metadata, then use Sync from your server.",
                        _ => null,
                    };
                    if (message is not null) return new(CompletionDisposition.Failed, status, message);
                }
            }
        }
        catch (Exception exception) when (exception is JsonException or InvalidOperationException or KeyNotFoundException or FormatException or OverflowException) { }
        return new(CompletionDisposition.Retry, status,
            status == 404 ? "This Sesn server does not yet support durable completions. Pending watches stay here."
            : status is 401 or 403 ? "Sesn rejected the connection. Pending watches stay here."
            : "Sesn has not confirmed this completion. It remains pending for retry.", retryAfterSeconds);
    }
}
