#!/usr/bin/env python3
"""Instant, zero-install test runner for this script ceapp — stdlib only, no pip, no pytest.

    python3 test.py            # run every test
    python3 test.py -k lease   # only tests whose name contains "lease"

Script ceapps have no build step, so their tests should be INSTANT. This runs every
`test_*` function in `tests/*.py` directly. A tiny built-in `pytest` shim (just `raises` +
`fixture`) means the same files also run under real `pytest` if you have it — but you never
need to install anything. Works anywhere Python does, including on the board.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys
import time
import traceback
import types


def _install_pytest_shim() -> None:
    if "pytest" in sys.modules:
        return
    shim = types.ModuleType("pytest")

    @contextlib.contextmanager
    def raises(exc, *_a, **_k):
        try:
            yield
        except exc:
            return
        except BaseException as e:  # noqa: BLE001 - report the wrong exception clearly
            raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
        raise AssertionError(f"expected {exc.__name__} to be raised, nothing was")

    def fixture(*a, **_k):
        if len(a) == 1 and callable(a[0]):
            return a[0]
        return lambda fn: fn

    shim.raises = raises
    shim.fixture = fixture
    shim.mark = types.SimpleNamespace(skip=lambda *a, **k: (lambda fn: fn))
    sys.modules["pytest"] = shim


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    _install_pytest_shim()

    filt = argv[argv.index("-k") + 1] if "-k" in argv else None
    passed = 0
    failures = []
    start = time.perf_counter()
    for file in sorted((root / "tests").glob("test_*.py")):
        module = _load(file)
        for name in sorted(vars(module)):
            if not name.startswith("test_") or (filt and filt not in name):
                continue
            fn = getattr(module, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
            except BaseException:  # noqa: BLE001 - a failed test must not stop the run
                failures.append((file.name, name, traceback.format_exc()))

    for fname, tname, tb in failures:
        print(f"FAIL {fname}::{tname}\n{tb}")
    elapsed = (time.perf_counter() - start) * 1000
    status = "OK" if not failures else "FAILED"
    print(f"{status}: {passed} passed, {len(failures)} failed in {elapsed:.0f} ms")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
