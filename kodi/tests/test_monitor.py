"""Exercise the shipped playback/pairing code with local Kodi API fixtures."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

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
sys.modules['scrobble_queue'] = types.SimpleNamespace(capture=lambda payload, **kw: sent.append(payload) or True)
sys.modules['account'] = types.SimpleNamespace(current=lambda: 'fixture-owner')
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

    def test_capture_has_frozen_dates_session_sequence_and_unique_event_ids(self):
        with patch.object(monitor.time, 'time', return_value=1767225599):
            self.player.onAVStarted()
        with patch.object(monitor.time, 'time', return_value=1767225601):
            self.player.onPlayBackEnded()
            self.player.onPlayBackStopped()  # duplicate teardown does not re-log
        self.assertEqual([p['watched_at'] for p in sent], [1767225599, 1767225601])
        self.assertEqual([p['sequence'] for p in sent], [0, 1])
        self.assertEqual(sent[0]['session_id'], sent[1]['session_id'])
        self.assertNotEqual(sent[0]['event_id'], sent[1]['event_id'])

    def test_disconnected_and_changed_owner_never_capture_old_playback(self):
        with patch.object(monitor.account, 'current', return_value=None):
            self.player.onAVStarted()
            self.player.onPlayBackEnded()
        self.assertEqual(sent, [])
        self.player.onAVStarted()
        with patch.object(monitor.account, 'current', return_value='another-owner'):
            self.player.onPlayBackEnded()
        self.assertEqual([p['action'] for p in sent], ['start'])

    def test_duplicate_av_callback_is_one_session_but_rewatch_is_new_session(self):
        self.player.onAVStarted()
        first = sent[0]['session_id']
        self.player.onAVStarted()
        self.assertEqual(len(sent), 1)
        self.player.onPlayBackEnded()
        self.player.onAVStarted()
        self.assertNotEqual(sent[-1]['session_id'], first)

    def test_seek_back_then_immediate_stop_cannot_use_stale_high_position(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.getTotalTime = lambda: 1000
        self.player.isPlaying = lambda: True
        self.player.getTime = lambda: 900  # engine still reports pre-seek sample
        self.player.onPlayBackSeek(100000, -800000)
        self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 10)

    def test_final_valid_sample_crossing_threshold_is_captured(self):
        self.player.onAVStarted()
        self.player._last_percent = 79
        self.player.getTotalTime = lambda: 1000
        self.player.isPlaying = lambda: True
        self.player.getTime = lambda: 810
        self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 81)

    def test_seek_with_nonpositive_or_invalid_duration_resets_stale_completion(self):
        for total in (0, -1, None, float('nan'), float('inf')):
            with self.subTest(total=total):
                self.player.onAVStarted()
                self.player._last_percent = 90
                self.player.getTotalTime = lambda: total
                self.player.isPlaying = lambda: False
                self.player.onPlayBackSeek(100000, -800000)
                self.player.onPlayBackStopped()
                self.assertEqual(sent[-1]['progress'], 0)

    def test_seek_unknown_duration_recovers_when_fresh_position_is_available(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.getTotalTime = lambda: 0
        self.player.onPlayBackSeek(100000, -800000)
        self.player.getTotalTime = lambda: 1000
        self.player.getTime = lambda: 100
        self.player.isPlaying = lambda: True
        self.player.sample_progress()
        self.assertEqual(self.player._last_percent, 10)
        self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 10)

    def test_next_item_position_cannot_overwrite_previous_session_before_av_ready(self):
        self.player.onAVStarted()
        previous_session = sent[-1]['session_id']
        self.player._last_percent = 90
        self.player.onPlayBackStarted()
        self.player.getTotalTime = lambda: 1000
        self.player.getTime = lambda: 0
        self.player.isPlaying = lambda: True
        self.player.sample_progress()
        self.assertEqual(self.player._last_percent, 90)
        self.player.tag = Tag(media='movie')
        self.player.onAVStarted()
        self.assertEqual([p['action'] for p in sent], ['start', 'stop', 'start'])
        self.assertEqual(sent[1]['session_id'], previous_session)
        self.assertEqual(sent[1]['progress'], 90)
        self.assertNotEqual(sent[2]['session_id'], previous_session)
        self.assertEqual(self.player._last_percent, 0)

    def test_pending_same_title_rewatch_preserves_previous_sample_through_seek_and_pause(self):
        self.player.onAVStarted()
        previous_session = sent[-1]['session_id']
        self.player._last_percent = 90
        self.player.onPlayBackStarted()
        self.player.getTotalTime = lambda: 1000
        self.player.getTime = lambda: 0
        self.player.isPlaying = lambda: True
        self.player.onPlayBackSeek(0, -900000)
        self.player.onPlayBackPaused()
        self.player.onPlayBackResumed()
        self.player.sample_progress()
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.player._last_percent, 90)
        self.player.onAVStarted()  # identical title, distinct explicitly started sitting
        self.assertEqual([p['action'] for p in sent], ['start', 'stop', 'start'])
        self.assertEqual(sent[1]['progress'], 90)
        self.assertEqual(sent[1]['session_id'], previous_session)
        self.assertNotEqual(sent[2]['session_id'], previous_session)

    def test_malformed_optional_movie_ids_keep_only_real_title_fallback(self):
        self.player.tag = Tag(media='movie')
        for value in ('movie/42', '0', '-1', '1.5', '9007199254740992', '9' * 5000, '１２３', ''):
            with self.subTest(value=value[:30]):
                self.player.tag.getUniqueID = lambda kind: 'not-imdb' if kind == 'imdb' else value
                self.assertEqual(self.player._build_ref(), {'type': 'movie', 'title': 'Movie title', 'year': 2025})

    def test_valid_movie_provider_ids_remain_positive_safe_json_numbers(self):
        self.player.tag = Tag(media='movie')
        self.player.tag.getUniqueID = lambda kind: {'tmdb': ' 00042 ', 'tvdb': '9007199254740991', 'imdb': ' tt1234567 '}[kind]
        self.assertEqual(self.player._build_ref(), {'type': 'movie', 'title': 'Movie title', 'year': 2025,
            'tmdb_id': 42, 'tvdb_id': 9007199254740991, 'imdb_id': 'tt1234567'})

    def test_missing_title_and_invalid_ids_do_not_invent_identity_or_capture(self):
        self.player.tag = Tag(media='movie')
        self.player.tag.getTitle = lambda: ''
        self.player.tag.getUniqueID = lambda kind: 'invalid'
        self.assertIsNone(self.player._build_ref())
        self.player.onAVStarted()
        self.player.onPlayBackEnded()
        self.assertEqual(sent, [])

    def test_valid_remaining_id_is_preserved_when_other_optional_ids_are_bad(self):
        self.player.tag = Tag(media='movie')
        self.player.tag.getTitle = lambda: ''
        self.player.tag.getUniqueID = lambda kind: '42' if kind == 'tvdb' else 'invalid'
        self.assertEqual(self.player._build_ref(), {'type': 'movie', 'title': '', 'year': 2025, 'tvdb_id': 42})

    def test_short_video_seek_guard_accepts_time_plausible_fresh_progress(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 10
        with patch.object(monitor.time, 'monotonic', return_value=100):
            self.player.onPlayBackSeek(1000, -8000)
        for position in range(2, 10):
            self.player.getTime = lambda position=position: position
            with patch.object(monitor.time, 'monotonic', return_value=100 + position - 1):
                self.player.sample_progress()
        self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 90)
        self.assertFalse(self.player._seek_pending)

    def test_delayed_duration_recovers_after_progress_exceeds_old_fixed_window(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 0
        with patch.object(monitor.time, 'monotonic', return_value=100):
            self.player.onPlayBackSeek(1000, -8000)
        self.player.getTotalTime = lambda: 10
        self.player.getTime = lambda: 5
        with patch.object(monitor.time, 'monotonic', return_value=104):
            self.player.sample_progress()
        self.assertEqual(self.player._last_percent, 50)
        self.assertFalse(self.player._seek_pending)

    def test_time_window_still_rejects_immediate_stale_preseek_position(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 10
        self.player.getTime = lambda: 9
        with patch.object(monitor.time, 'monotonic', return_value=100):
            self.player.onPlayBackSeek(1000, -8000)
            self.player.sample_progress()
            self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 10)

    def test_fractional_position_is_not_rounded_across_completion_threshold(self):
        self.player.onAVStarted()
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 1000
        self.player.getTime = lambda: 796
        self.player.onPlayBackStopped()
        self.assertAlmostEqual(sent[-1]['progress'], 79.6)
        self.assertLess(sent[-1]['progress'], 80)

    def test_paused_time_cannot_make_stale_preseek_completion_plausible(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 10
        self.player.getTime = lambda: 9
        with patch.object(monitor.time, 'monotonic', return_value=100):
            self.player.onPlayBackSeek(1000, -8000)
            self.player.onPlayBackPaused()
        with patch.object(monitor.time, 'monotonic', return_value=120):
            self.player.onPlayBackResumed()
            self.player.onPlayBackStopped()
        self.assertEqual(sent[-1]['progress'], 10)

    def test_speed_changes_use_only_time_spent_at_each_speed(self):
        self.player.onAVStarted()
        self.player._last_percent = 90
        self.player.isPlaying = lambda: True
        self.player.getTotalTime = lambda: 10
        with patch.object(monitor.time, 'monotonic', return_value=100):
            self.player.onPlayBackSeek(1000, -8000)
            self.player.onPlayBackSpeedChanged(2)
        self.player.getTime = lambda: 3
        with patch.object(monitor.time, 'monotonic', return_value=101):
            self.player.sample_progress()
        self.assertEqual(self.player._last_percent, 30)
        self.assertFalse(self.player._seek_pending)


if __name__ == '__main__':
    unittest.main()
