"""Build local Kodi release artifacts. This does not commit, push or publish."""
from hashlib import md5
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
source = ROOT / 'script.sesn'
version = ET.parse(source / 'addon.xml').getroot().attrib['version']
archive = ROOT / 'repo/script.sesn' / ('script.sesn-' + version + '.zip')
files = sorted(p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as zipped:
    for file in files:
        info = ZipInfo('script.sesn/' + file.relative_to(source).as_posix(), date_time=(2026, 9, 21, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zipped.writestr(info, file.read_bytes())

def fragment(path):
    text = path.read_text(encoding='utf-8')
    return text[text.index('<addon '):].strip()

index = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + '\n'.join([
    fragment(ROOT / 'repository.sesn/addon.xml'), fragment(source / 'addon.xml')]) + '\n</addons>\n'
(ROOT / 'repo/addons.xml').write_text(index, encoding='utf-8')
(ROOT / 'repo/addons.xml.md5').write_text(md5(index.encode('utf-8')).hexdigest(), encoding='ascii')
with ZipFile(archive) as zipped:
    assert zipped.testzip() is None
    assert ET.fromstring(zipped.read('script.sesn/addon.xml')).attrib['version'] == version
    assert b'"provider": "kodi"' in zipped.read('script.sesn/resources/lib/pair.py')
    assert b'_parent_show' in zipped.read('script.sesn/resources/lib/monitor.py')
print('Built local release', version, 'with', len(files), 'files; not published.')
