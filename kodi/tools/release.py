#!/usr/bin/env python3
"""Build the Kodi repository tree that repository.sesn reads.

Since repository.sesn 1.0.1 the repository is served from the dedicated
`kodi-repo` branch of github.com/sesn-io/sesn-integrations (protected; only this
script's output is committed there). The branch root looks like:

    addons.xml                     index of every add-on's addon.xml
    addons.xml.sha256              checked by Kodi (<checksum verify="sha256">)
    addons.xml.md5                 for older tooling only
    script.sesn/script.sesn-X.Y.Z.zip
    script.sesn/script.sesn-X.Y.Z.zip.sha256   checked by Kodi (<hashes>sha256</hashes>)
    script.sesn/icon.png, fanart.jpg
    repository.sesn/repository.sesn-X.Y.Z.zip (+ .sha256, icon.png)

Usage (from the repository root, with the kodi-repo branch checked out in a
separate worktree):

    git worktree add ../kodi-repo kodi-repo
    python3 kodi/tools/release.py --out ../kodi-repo
    # review, then commit and push in ../kodi-repo

The old location read by repository.sesn 1.0.0 (main:kodi/repo) has the same
layout, so `--out kodi/repo` refreshes it. Publish one release there containing
repository.sesn 1.0.1 so existing installs pick up the new address; after that
main:kodi/repo can be frozen.

Existing zips are never rewritten: a version that is already published with
different contents is an error (bump the version instead). This script does not
commit, push or publish anything.
"""
import argparse
from hashlib import md5, sha256
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

KODI = Path(__file__).resolve().parents[1]
ADDONS = ("repository.sesn", "script.sesn")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # deterministic zips: same input, same bytes
SKIP_DIRS = {"__pycache__", "tests", ".git"}


def addon_version(source):
    root = ET.parse(source / "addon.xml").getroot()
    if root.attrib.get("id") != source.name:
        raise SystemExit("%s/addon.xml has id %r" % (source, root.attrib.get("id")))
    return root.attrib["version"], root


def build_zip(source, version):
    """Return the zip bytes for one add-on folder."""
    from io import BytesIO

    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and not (SKIP_DIRS & set(p.relative_to(source).parts)) and p.suffix not in (".pyc", ".pyo")
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zipped:
        for file in files:
            info = ZipInfo(source.name + "/" + file.relative_to(source).as_posix(), date_time=FIXED_TIME)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zipped.writestr(info, file.read_bytes())
    data = buffer.getvalue()
    with ZipFile(BytesIO(data)) as zipped:
        assert zipped.testzip() is None
        inner = ET.fromstring(zipped.read(source.name + "/addon.xml"))
        assert inner.attrib["version"] == version
    return data, len(files)


def fragment(path):
    text = path.read_text(encoding="utf-8")
    return text[text.index("<addon "):].strip()


def write_digest(path, data, name):
    path.write_text("%s  %s\n" % (sha256(data).hexdigest(), name), encoding="ascii")


def release(out):
    out.mkdir(parents=True, exist_ok=True)
    for addon in ADDONS:
        source = KODI / addon
        version, root = addon_version(source)
        data, count = build_zip(source, version)
        target_dir = out / addon
        target_dir.mkdir(exist_ok=True)
        name = "%s-%s.zip" % (addon, version)
        target = target_dir / name
        if target.exists() and target.read_bytes() != data:
            raise SystemExit(
                "%s already exists with different contents. Published versions are immutable: "
                "bump the version in %s/addon.xml." % (target, addon)
            )
        target.write_bytes(data)
        write_digest(target_dir / (name + ".sha256"), data, name)
        for asset in root.iter():
            if asset.tag in ("icon", "fanart") and asset.text and (source / asset.text).is_file():
                shutil.copyfile(source / asset.text, target_dir / Path(asset.text).name)
        print("%-16s %-8s %3d files  sha256 %s" % (addon, version, count, sha256(data).hexdigest()))

    index = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n'
        + "\n".join(fragment(KODI / addon / "addon.xml") for addon in ADDONS)
        + "\n</addons>\n"
    ).encode("utf-8")
    ET.fromstring(index)  # well-formed
    (out / "addons.xml").write_bytes(index)
    # Kodi reads the first token, so "<hex>  addons.xml" is fine for the sha256 file.
    write_digest(out / "addons.xml.sha256", index, "addons.xml")
    # repository.sesn 1.0.0 (old location) reads a bare md5 here.
    (out / "addons.xml.md5").write_text(md5(index).hexdigest(), encoding="ascii")
    print("Wrote %s (sha256 %s). Nothing was committed or pushed." % (out / "addons.xml", sha256(index).hexdigest()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="root of the kodi-repo branch checkout, or kodi/repo for the old location")
    args = parser.parse_args(argv)
    release(args.out.resolve())


if __name__ == "__main__":
    sys.exit(main())
