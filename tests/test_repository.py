"""Offline documentation and published Kodi artifact consistency checks.

Run from the repository root: python3 -m unittest discover -s tests -v
No network requests, client execution, packaging or release writes are performed.
"""
from hashlib import md5, sha256
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", "bin", "obj", "artifacts", "__pycache__"}


def documentation():
    return sorted(path for path in ROOT.rglob("*.md")
                  if not IGNORED.intersection(path.relative_to(ROOT).parts))


def heading_ids(text):
    seen = {}
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


class DocumentationTests(unittest.TestCase):
    def test_relative_documentation_links_and_fragments(self):
        checked = 0
        for document in documentation():
            for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                path = (document.parent / unquote(url.path)).resolve() if url.path else document
                with self.subTest(document=document.relative_to(ROOT), target=target):
                    self.assertTrue(path.is_relative_to(ROOT), "Link escapes repository")
                    self.assertTrue(path.exists(), "Missing local link target")
                    if path.is_dir():
                        path = path / "README.md"
                        self.assertTrue(path.exists(), "Linked directory lacks README")
                    if url.fragment and path.suffix == ".md":
                        self.assertIn(unquote(url.fragment), heading_ids(path.read_text(encoding="utf-8")))
                checked += 1
        self.assertGreater(checked, 0)

    def test_documented_jellyfin_build_matrix_matches_packager(self):
        script = (ROOT / "jellyfin/package.mjs").read_text(encoding="utf-8")
        guide = (ROOT / "jellyfin/README.md").read_text(encoding="utf-8")
        builds = re.findall(r"jellyfin: '([^']+)'.*?framework: '([^']+)'.*?version: '([^']+)'.*?abi: '([^']+)'", script)
        self.assertEqual(len(builds), 3)
        for server, framework, version, abi in builds:
            with self.subTest(version=version):
                rows = [line for line in guide.splitlines() if line.startswith("|") and version in line]
                self.assertEqual(len(rows), 1)
                self.assertIn(server, rows[0])
                self.assertIn(abi, rows[0])
                self.assertIn(".NET " + framework.removeprefix("net").split(".")[0], rows[0])

    def test_local_tautulli_credentials_are_ignored(self):
        entries = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("sesn-tautulli.json", entries)


class KodiArtifactTests(unittest.TestCase):
    def test_stored_release_index_digests(self):
        repository = ROOT / "kodi/repo"
        data = (repository / "addons.xml").read_bytes()
        self.assertEqual((repository / "addons.xml.md5").read_text().strip(), md5(data).hexdigest())
        digest, name = (repository / "addons.xml.sha256").read_text().split()
        self.assertEqual(name, "addons.xml")
        self.assertEqual(digest, sha256(data).hexdigest())

    def test_index_entries_match_published_zip_metadata_and_hashes(self):
        repository = ROOT / "kodi/repo"
        addons = list(ET.parse(repository / "addons.xml").getroot())
        self.assertEqual({addon.attrib["id"] for addon in addons}, {"script.sesn", "repository.sesn"})
        for addon in addons:
            identifier, version = addon.attrib["id"], addon.attrib["version"]
            filename = f"{identifier}-{version}.zip"
            archive = repository / identifier / filename
            with self.subTest(addon=identifier, version=version):
                digest, name = archive.with_suffix(".zip.sha256").read_text().split()
                self.assertEqual(name, filename)
                self.assertEqual(digest, sha256(archive.read_bytes()).hexdigest())
                with ZipFile(archive) as zipped:
                    self.assertIsNone(zipped.testzip())
                    packaged = ET.fromstring(zipped.read(f"{identifier}/addon.xml"))
                # Only compare released artifacts with the released index. Current
                # source metadata may intentionally differ before its next release.
                self.assertEqual(ET.tostring(packaged).strip(), ET.tostring(addon).strip())

    def test_repository_installer_copy_is_identical(self):
        addon = ET.parse(ROOT / "kodi/repository.sesn/addon.xml").getroot()
        filename = f"repository.sesn-{addon.attrib['version']}.zip"
        self.assertEqual((ROOT / "kodi" / filename).read_bytes(),
                         (ROOT / "kodi/repo/repository.sesn" / filename).read_bytes())

    def test_all_stored_historical_zip_versions_are_consistent(self):
        archives = sorted((ROOT / "kodi/repo").rglob("*.zip"))
        self.assertGreater(len(archives), 0)
        for archive in archives:
            with self.subTest(archive=archive.name), ZipFile(archive) as zipped:
                self.assertIsNone(zipped.testzip())
                addon_id = archive.parent.name
                addon = ET.fromstring(zipped.read(f"{addon_id}/addon.xml"))
                self.assertEqual(addon.attrib["id"], addon_id)
                self.assertEqual(archive.name, f"{addon_id}-{addon.attrib['version']}.zip")
                self.assertTrue(all(name.startswith(addon_id + "/") and ".." not in Path(name).parts
                                    for name in zipped.namelist()))


if __name__ == "__main__":
    unittest.main()
