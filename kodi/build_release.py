"""Superseded by kodi/tools/release.py (repository.sesn 1.0.1 reads the kodi-repo branch).

Kept so the old command still works: it refreshes the old location (kodi/repo),
which repository.sesn 1.0.0 installs still read. Nothing is committed or pushed.
"""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
sys.argv = [str(ROOT / 'tools/release.py'), '--out', str(ROOT / 'repo')]
runpy.run_path(str(ROOT / 'tools/release.py'), run_name='__main__')
