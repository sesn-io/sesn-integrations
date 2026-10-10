"""All browse metadata is enriched in bounded, paced requests."""
import sys
import types
import unittest
from unittest.mock import patch

from test_security import Kodi, load


class BrowseIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.kodi = Kodi()
        self.calls = []
        self.fail_at = None
        def get_json(path):
            refs = path.split('ids=')[1].split(',')
            self.calls.append(refs)
            if len(self.calls) == self.fail_at:
                return None
            return {'ok': True, 'items': [{'type': ref.split(':')[0], 'tmdb_id': int(ref.split(':')[1]),
                                          'imdb_id': 'tt' + ref.split(':')[1]} for ref in refs]}
        self.api = types.SimpleNamespace(get_json=get_json)
        with patch.object(sys, 'argv', ['plugin://script.sesn/', '1', '']):
            self.plugin = load('plugin', self.kodi, {'sesn_api': self.api, 'kodi_library': types.SimpleNamespace(),
                                                    'xbmcplugin': types.SimpleNamespace()})

    def test_120_items_match_imdb_only_library_across_every_chunk(self):
        items = [{'type': 'movie', 'tmdb_id': i} for i in range(1, 121)]
        enriched = self.plugin._enrich_ids(items)
        self.assertEqual([len(c) for c in self.calls], [50, 50, 20])
        self.assertEqual(self.kodi.waits, [0.25, 0.25])
        library = load('kodi_library', self.kodi)
        index = {'imdb:tt%d' % i: i + 1000 for i in range(1, 121)}
        for i in (1, 50, 51, 100, 120):
            item = enriched[i - 1]
            self.assertEqual(library.find_movie({'imdb': item['imdb_id']}, index), i + 1000)

    def test_duplicate_refs_are_fetched_once(self):
        items = [{'type': 'tv', 'tmdb_id': 1}, {'type': 'tv', 'tmdb_id': 1}]
        self.plugin._enrich_ids(items)
        self.assertEqual(self.calls, [['tv:1']])
        self.assertEqual([i['imdb_id'] for i in items], ['tt1', 'tt1'])

    def test_partial_failure_keeps_known_ids_and_shows_incomplete_lookup(self):
        self.fail_at = 2
        items = [{'type': 'movie', 'tmdb_id': i, 'tvdb_id': i + 500} for i in range(1, 121)]
        self.plugin._enrich_ids(items)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(items[0]['tvdb_id'], 501)
        self.assertEqual(items[49]['imdb_id'], 'tt50')
        self.assertNotIn('imdb_id', items[50])
        self.assertTrue(any('incomplete' in line for line in self.kodi.logs))
        self.assertEqual(len(self.kodi.notes), 1)


if __name__ == '__main__':
    unittest.main()
