"""Shared test scaffolding.

Import this module anywhere in the suite and every temporary directory the
process creates is removed when it ends. Only one test file used to clean up
after itself, so a full run left 184 directories and 2 MB in $TMPDIR -- and
test_meta starts the run twice more as a subprocess.

The wrapper sits on tempfile.mkdtemp rather than on each call site: there are
56 of them across nine files, and a helper that has to be remembered at every
call is a helper that gets forgotten at one of them. This project has met that
pattern often enough.
"""
import atexit
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

# No .pyc beside the plugin. Loading bin/plugin-inventar as a module writes
# bin/__pycache__, and the tool then reports it as "liegt da, wird nicht
# geladen" -- the suite changing the very thing it measures.
sys.dont_write_bytecode = True

_created = []
_original = tempfile.mkdtemp


def load_tool():
    """bin/plugin-inventar as a module, for testing its helpers directly."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bin", "plugin-inventar")
    loader = importlib.machinery.SourceFileLoader("plugin_inventar_cli", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _remembering(*args, **kwargs):
    path = _original(*args, **kwargs)
    _created.append(path)
    return path


if tempfile.mkdtemp is _original:
    tempfile.mkdtemp = _remembering

    @atexit.register
    def _clean():
        tempfile.mkdtemp = _original
        for path in _created:
            shutil.rmtree(path, ignore_errors=True)
