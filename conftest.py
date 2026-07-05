"""Test bootstrap: put the app root on sys.path so ``import ce`` (vendored), ``capauth``,
and the ``climate`` package resolve during ``pytest`` runs."""

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
