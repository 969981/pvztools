"""Bundled package data.

``lineups.json`` -- the ArrayDesign lineup catalogue (363 无尽阵型), exported
from LonelyStar's web lineup designer (http://lonelystar.org/ArrayDesign).
The working copy in ``docs/research/arraydesign-lineups.json`` is the same
file; this one is the canonical copy shipped in the wheel.

license_note:
    The lineup catalogue was collected and published by LonelyStar
    (植吧 "囧丫乙", lonelystar.org).  Redistribution inside pvztools is by
    explicit permission of the author.  The data is *not* covered by the
    pvztools MIT license -- do not re-license or strip this attribution.
"""
from __future__ import annotations

import atexit
import contextlib
import json
import pathlib
from importlib.resources import as_file, files
from typing import Any, Dict, List

__all__ = ["lineups_path", "load_lineups", "load_lineups_raw", "LINEUPS_FILE"]

LINEUPS_FILE = "lineups.json"

#: Keeps ``as_file`` extractions (zipped installs) alive for the whole
#: process: the temporary file is removed only at interpreter exit.
_EXTRACTED = contextlib.ExitStack()
atexit.register(_EXTRACTED.close)
_EXTRACTED_PATHS: Dict[str, pathlib.Path] = {}


def lineups_path():
    """Filesystem path of the bundled ``lineups.json`` (``pathlib.Path``).

    Uses ``importlib.resources.files`` (py3.9+).  For a plain install the
    traversable *is* the file; for a zipped install (zipimport / egg) the
    resource is extracted with ``as_file`` and the extraction is kept alive
    for the process lifetime (an ``ExitStack`` closed ``atexit``), so the
    returned path stays valid -- it is captured once at CLI import.
    """
    res = files(__name__) / LINEUPS_FILE
    try:
        p = pathlib.Path(str(res))
        if p.is_file():
            return p
    except Exception:  # pragma: no cover
        pass
    return _extracted(res, LINEUPS_FILE)


def _extracted(res: Any, key: str) -> pathlib.Path:
    """``as_file(res)`` whose context stays open until exit (memoised)."""
    p = _EXTRACTED_PATHS.get(key)
    if p is None or not p.is_file():
        p = pathlib.Path(_EXTRACTED.enter_context(as_file(res)))
        _EXTRACTED_PATHS[key] = p
    return p


def load_lineups_raw() -> Dict[str, Any]:
    """The whole JSON document (``source`` / ``license_note`` / ``lineups`` ...)."""
    return json.loads((files(__name__) / LINEUPS_FILE).read_text(encoding="utf-8"))


def load_lineups() -> List[Dict[str, Any]]:
    """The ``lineups`` list (363 entries: ``index`` / ``name`` / ``scene`` / ``raw`` ...)."""
    data = load_lineups_raw()
    entries = data["lineups"] if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("%s: 期望 'lineups' 列表" % LINEUPS_FILE)
    return entries
