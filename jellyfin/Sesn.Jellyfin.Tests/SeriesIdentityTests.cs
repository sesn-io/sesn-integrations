using System.Net;
using System.Text.Json;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.TV;
using MediaBrowser.Controller.Library;
using Moq;

static class SeriesIdentityTests
{
    public static (string Name, Func<Task> Run)[] Cases =>
    [
        ("native episodes distinguish parent-series IDs from episode IDs", async () =>
        {
            using var library = new SeriesLibrary();
            using var fixture = new Fixture();
            var payload = await Capture(fixture, library.Episode);
            Check(payload.GetProperty("SesnPayloadVersion").GetInt32() == 1, "Native payload does not identify its parent-series contract.");
            Check(payload.GetProperty("SeriesProvider_tmdb").GetString() == "123", "Parent series TMDB ID was lost.");
            Check(payload.GetProperty("SeriesProvider_tvdb").GetString() == "456", "Parent series TVDB ID was lost.");
            Check(payload.GetProperty("SeriesProvider_imdb").GetString() == "tt789", "Parent series IMDb ID was lost.");
            Check(payload.GetProperty("Provider_tmdb").GetString() == "999999", "Episode ID was replaced by its series ID.");
        }),
        ("native series IDs preserve specials and combined episode ranges", async () =>
        {
            using var library = new SeriesLibrary();
            using var fixture = new Fixture();
            var payload = await Capture(fixture, library.Episode);
            Check(payload.GetProperty("SeasonNumber").GetInt32() == 0 && payload.GetProperty("EpisodeNumber").GetInt32() == 1 &&
                payload.GetProperty("EpisodeNumberEnd").GetInt32() == 2, "Series identity changed special/range numbering.");
            Check(payload.GetProperty("SeriesName").GetString() == "Localized custom title", "Name fallback was lost.");
            Check(payload.GetProperty("SeriesPremiereDate").ValueKind == JsonValueKind.Null, "Missing premiere year was fabricated.");
        }),
        ("alternate local numbering is preserved without inferred reordering", async () =>
        {
            using var library = new SeriesLibrary(12, 87);
            using var fixture = new Fixture();
            var payload = await Capture(fixture, library.Episode);
            Check(payload.GetProperty("SeasonNumber").GetInt32() == 12 && payload.GetProperty("EpisodeNumber").GetInt32() == 87,
                "Native identity guessed a different episode ordering.");
        }),
        ("missing series never promotes episode provider IDs to series identity", async () =>
        {
            using var library = new SeriesLibrary();
            library.Episode.SeriesId = Guid.Empty;
            using var fixture = new Fixture();
            var payload = await Capture(fixture, library.Episode);
            Check(!payload.TryGetProperty("SeriesProvider_tmdb", out _), "Episode metadata was substituted for missing parent identity.");
            Check(payload.GetProperty("Provider_tmdb").GetString() == "999999", "Missing parent removed genuine episode metadata.");
        }),
    ];
    private static async Task<JsonElement> Capture(Fixture fixture, Episode item)
    {
        JsonElement captured = default;
        fixture.Respond = async (request, _) =>
        {
            using var json = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
            captured = json.RootElement.Clone();
            return new HttpResponseMessage(HttpStatusCode.OK);
        };
        await fixture.PlayItem(item);
        Check(captured.ValueKind == JsonValueKind.Object, "Native event was not captured.");
        return captured;
    }
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private sealed class SeriesLibrary : IDisposable
    {
        private readonly ILibraryManager _previous = BaseItem.LibraryManager;
        public Episode Episode { get; }
        public SeriesLibrary(int seasonNumber = 0, int episodeNumber = 1)
        {
            var series = new Series { Id = Guid.NewGuid(), Name = "Localized custom title",
                ProviderIds = new Dictionary<string, string> { ["Tmdb"] = "123", ["Tvdb"] = "456", ["Imdb"] = "tt789" } };
            var season = new Season { Id = Guid.NewGuid(), IndexNumber = seasonNumber, SeriesId = series.Id };
            Episode = new Episode { Id = Guid.NewGuid(), Name = "Synthetic combined special", SeriesId = series.Id, SeasonId = season.Id, IndexNumber = episodeNumber, IndexNumberEnd = episodeNumber + 1,
                ProviderIds = new Dictionary<string, string> { ["Tmdb"] = "999999", ["Tvdb"] = "888888" } };
            var library = new Mock<ILibraryManager>();
            library.Setup(manager => manager.GetItemById<Series>(series.Id)).Returns(series);
            library.Setup(manager => manager.GetItemById<Season>(season.Id)).Returns(season);
            library.Setup(manager => manager.GetItemById(series.Id)).Returns(series);
            library.Setup(manager => manager.GetItemById(season.Id)).Returns(season);
            BaseItem.LibraryManager = library.Object;
        }
        public void Dispose() => BaseItem.LibraryManager = _previous;
    }
}
