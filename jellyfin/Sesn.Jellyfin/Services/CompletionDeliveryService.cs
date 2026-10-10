using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sesn.Jellyfin.Services;

/// <summary>Replays completed watches only after authenticated capability and ownership checks.</summary>
public sealed class CompletionDeliveryService(SesnApiClient client, ILogger<CompletionDeliveryService> logger) : BackgroundService
{
    private readonly SemaphoreSlim _deliveryGate = new(1, 1);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await TickAsync(stoppingToken).ConfigureAwait(false); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogWarning(exception, "Sesn completion storage or delivery failed"); }
            await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken).ConfigureAwait(false);
        }
    }

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        if (!await _deliveryGate.WaitAsync(0, cancellationToken).ConfigureAwait(false)) return;
        try
        {
            for (var count = 0; count < 10; count++)
            {
                var plugin = Plugin.Instance;
                if (plugin is null || !plugin.TryGetDelivery(out var state, out var key)) return;
                var authorization = plugin.GetCompletionAuthorization(state, key);
                if (authorization is null) return;
                var item = plugin.Completions.NextDue(authorization);
                if (item is null) { plugin.RefreshCompletionStatus(); return; }
                using var delivery = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, state.CancellationToken);
                try
                {
                    var response = await client.SendCompletionAsync(state.Configuration, key, item, delivery.Token).ConfigureAwait(false);
                    if (response.Disposition == CompletionDisposition.Accepted)
                        plugin.Completions.Acknowledge(item.EventId, item.Owner);
                    else if (response.Disposition == CompletionDisposition.Failed)
                        plugin.Completions.Fail(item.EventId, response.Message!);
                    else
                        plugin.Completions.Retry(item.EventId, response.Message!, response.RetryAfterSeconds);
                    if (response.Status is 401 or 403) plugin.MarkCredentialRejected(state);
                }
                catch (OperationCanceledException) when (delivery.IsCancellationRequested) { return; }
                catch (Exception exception) when (exception is HttpRequestException or IOException)
                {
                    plugin.Completions.Retry(item.EventId, "Sesn is unavailable. This completion remains pending for retry.");
                }
                catch (OperationCanceledException)
                {
                    plugin.Completions.Retry(item.EventId, "Sesn did not reply in time. This completion remains pending for retry.");
                }
                finally { plugin.RefreshCompletionStatus(); }
            }
        }
        finally { _deliveryGate.Release(); }
    }
}
