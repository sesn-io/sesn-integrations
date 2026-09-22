"""Exercise the shipped playback/pairing code with local Kodi API fixtures."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

LIB = Path(__file__).resolve().parents[1] / 'script.sesn' / 'resources' / 'lib'
sys.path.insert(0, str(LIB))
settings = {'scrobble_movies': True, 'scrobble_episodes': True, 'notify': False}
calls, sent = [], []
responses = {}


class Tag:
    def __init__(self, media='episode', dbid=71):
        self.media, self.dbid = media, dbid

    def getMediaType(self): return self.media
    def getDbId(self): return self.dbid
    def getUniqueID(self, kind): return {'tmdb': '987654', 'tvdb': '887654', 'imdb': 'tt9876543'}[kind]
    def getTVShowTitle(self): return 'Fallback show' if self.media != 'movie' else ''
    def getTitle(self): return 'Movie title' if self.media == 'movie' else 'Episode title'
    def getSeason(self): return 1
    def getEpisode(self): return 2
    def getYear(self): return 2025  # Episode year must not become the series year.


class Player:
    def __init__(self):
        self.tag, self.path, self.video = Tag(), '/library/episode.mkv', True
    def isPlayingVideo(self): return self.video
    def getPlayingFile(self): return self.path
    def getVideoInfoTag(self): return self.tag


def rpc(raw):
    request = json.loads(raw)
    calls.append(request)
    response = responses.get(request['method'], {})
    if isinstance(response, Exception):
        raise response
    return json.dumps({'result': response})


sys.modules['xbmc'] = types.SimpleNamespace(Player=Player, executeJSONRPC=rpc, log=lambda *args: None, LOGWARNING=2)
sys.modules['xbmcaddon'] = types.SimpleNamespace(Addon=lambda: types.SimpleNamespace(getSettingBool=lambda name: settings.get(name, False)))
sys.modules['xbmcgui'] = types.SimpleNamespace(Dialog=lambda: types.SimpleNamespace(notification=lambda *args: None), NOTIFICATION_INFO=0, NOTIFICATION_ERROR=1)
sys.modules['scrobble_queue'] = types.SimpleNamespace(enqueue=lambda payload: sent.append(payload) or True)
sys.modules['sesn_api'] = types.SimpleNamespace(post_json=lambda path, body: sent.append((path, body)))
monitor = importlib.import_module('monitor')
pair = importlib.import_module('pair')


class PlaybackIdentityTests(unittest.TestCase):
    def setUp(self):
        calls.clear(); sent.clear(); responses.clear()
        settings.update(scrobble_movies=True, scrobble_episodes=True, notify=False)
        responses.update({
            'VideoLibrary.GetEpisodeDetails': {'episodedetails': {'tvshowid': 500}},
            'VideoLibrary.GetTVShowDetails': {'tvshowdetails': {
                'title': 'Parent show', 'year': 1999,
                'uniqueid': {'tmdb': '444', 'tvdb': 555, 'imdb': 'tt1234567'},
            }},
        })
        self.player = monitor.SesnPlayer()

    def test_episode_uses_parent_series_identity_and_year(self):
        self.assertEqual(self.player._build_ref(), {
            'type': 'episode', 'title': 'Parent show', 'year': 1999,
            'season': 1, 'episode': 2, 'tmdb_id': 444, 'tvdb_id': 555, 'imdb_id': 'tt1234567',
        })
        self.assertEqual(calls[0]['params'], {'episodeid': 71, 'properties': ['tvshowid']})
        self.assertEqual(calls[1]['params']['tvshowid'], 500)

    def test_nonlibrary_episode_falls_back_without_episode_ids_or_episode_year(self):
        self.player.tag = Tag(dbid=-1)
        self.assertEqual(self.player._build_ref(), {'type': 'episode', 'title': 'Fallback show', 'season': 1, 'episode': 2})
        self.assertEqual(calls, [])

    def test_parent_lookup_failures_do_not_fall_back_to_episode_ids(self):
        for response in [{}, {'episodedetails': {'tvshowid': -1}}, RuntimeError('fixture failure')]:
            responses['VideoLibrary.GetEpisodeDetails'] = response
            self.assertEqual(self.player._build_ref(), {'type': 'episode', 'title': 'Fallback show', 'season': 1, 'episode': 2})

    def test_missing_or_malformed_show_details_are_safe(self):
        for details in [None, 'bad', {'uniqueid': []}, {'uniqueid': {'tmdb': 'bad', 'tvdb': -1, 'imdb': 'bad'}}]:
            responses['VideoLibrary.GetTVShowDetails'] = {'tvshowdetails': details}
            self.assertEqual(self.player._build_ref(), {'type': 'episode', 'title': 'Fallback show', 'season': 1, 'episode': 2})

    def test_movies_keep_their_own_ids_without_series_lookup(self):
        self.player.tag = Tag(media='movie')
        self.assertEqual(self.player._build_ref(), {
            'type': 'movie', 'title': 'Movie title', 'year': 2025,
            'tmdb_id': 987654, 'tvdb_id': 887654, 'imdb_id': 'tt9876543',
        })
        self.assertEqual(calls, [])

    def test_completion_and_partial_stop_retain_parent_reference(self):
        self.player.onAVStarted()
        self.player._last_percent = 50
        self.player.onPlayBackStopped()
        self.player.drain()
        self.assertEqual([event['action'] for event in sent], ['start', 'stop'])
        self.assertEqual(sent[-1]['progress'], 50)
        self.assertEqual(sent[-1]['tmdb_id'], 444)
        self.player.onAVStarted()
        self.player.onPlayBackEnded()
        self.player.drain()
        self.assertEqual(sent[-1]['progress'], 100)
        self.assertEqual(sent[-1]['tmdb_id'], 444)

    def test_pvr_nonvideo_and_disabled_episode_tracking_stay_excluded(self):
        self.player.path = 'pvr://channels/tv/1'
        self.assertIsNone(self.player._build_ref())
        self.player.path = '/library/episode.mkv'; self.player.video = False
        self.assertIsNone(self.player._build_ref())
        self.player.video = True; settings['scrobble_episodes'] = False
        self.assertIsNone(self.player._build_ref())
        self.assertEqual(calls, [])

    def test_pairing_identifies_the_provider_explicitly(self):
        pair.run()
        self.assertEqual(sent, [('/api/v1/link/new', {'device_name': 'Kodi', 'provider': 'kodi'})])


if __name__ == '__main__':
    unittest.main()
