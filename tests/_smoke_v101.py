"""Smoke v1.78.2: a plugin reload actually reloads this plugin's bundles.

AstrBot's reload purges only `data.plugins.<dir>.*` modules. This plugin puts
its own directory on sys.path and imports hippocampus / handlers / page_api as
TOP-LEVEL packages, so those survived a reload: main.py was re-executed while
the previous load's code stayed cached (observed live 2026-10-29: metadata said
1.78.1 while the banner still reported v1.77.0, and every v1.77.1+ fix was
inactive). main._evict_stale_bundles() drops exactly the modules that live
under this plugin directory, before the bundles are imported.

Tests:
- imported as top-level "main" (the smoke-test path) import-time eviction is off
- eviction drops owned roots AND their submodules
- a same-named module owned by another directory is left alone
- re-importing after eviction executes the package again (fresh module object)
"""
import importlib
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from _smoke_common import install_stub

install_stub()
import main  # noqa: E402

FAILS = []
CHECKS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    CHECKS.append(bool(ok))
    if not ok:
        FAILS.append(msg)


# ---- 1. test path never evicts at import time ------------------------------
check(main._EVICT_ON_IMPORT is False,
      "import-time eviction off when imported as top-level main")
check(main.EVICTED_ON_IMPORT == [],
      "nothing evicted at import time in the test process")
check(main._EVICT_ON_IMPORT == ("." in main.__name__),
      "host detection is 'dotted module name'")

# ---- 2. eviction drops owned modules, keeps foreign ones -------------------
real = {n: sys.modules[n] for n in list(sys.modules)
        if n.split(".")[0] in main._BUNDLE_ROOTS}


def fake(name, path):
    m = types.ModuleType(name)
    m.__file__ = path
    return m


for n in list(real):
    del sys.modules[n]
sys.modules["hippocampus"] = fake(
    "hippocampus", os.path.join(ROOT, "hippocampus", "__init__.py"))
sys.modules["hippocampus.storage"] = fake(
    "hippocampus.storage", os.path.join(ROOT, "hippocampus", "storage.py"))
sys.modules["handlers"] = fake(
    "handlers", os.path.join(ROOT, "handlers", "__init__.py"))
sys.modules["engram_command_registry"] = fake(
    "engram_command_registry", os.path.join(ROOT, "engram_command_registry.py"))
other_dir = tempfile.mkdtemp(prefix="engram_other_owner_")
sys.modules["page_api"] = fake("page_api", os.path.join(other_dir, "page_api.py"))

gone = main._evict_stale_bundles()
check(sorted(gone) == sorted(["hippocampus", "hippocampus.storage",
                              "handlers", "engram_command_registry"]),
      "eviction reports exactly the owned modules: " + repr(gone))
check("hippocampus" not in sys.modules and "hippocampus.storage" not in sys.modules,
      "owned package and its submodule are gone")
check("handlers" not in sys.modules, "handlers bundle is gone")
check("engram_command_registry" not in sys.modules, "root module is gone")
check("page_api" in sys.modules,
      "same-named module owned by another directory survives")

# ---- 3. re-import after eviction really executes the code again ------------
old = real["hippocampus"]
new = importlib.import_module("hippocampus")
check(new is not old, "re-import after eviction yields a fresh module object")
check(getattr(new, "__version__", "") == getattr(old, "__version__", "?"),
      "re-imported version matches the on-disk code")

# ---- restore the test process ---------------------------------------------
del sys.modules["page_api"]
for n in [n for n in list(sys.modules) if n.split(".")[0] == "hippocampus"]:
    del sys.modules[n]
sys.modules.update(real)

# ---- 4. end-to-end: host-style (dotted) load drops the cached package ----
# Mirrors what AstrBot does on reload: import the plugin as a dotted module
# while the previous load's hippocampus package is still in sys.modules.
import subprocess  # noqa: E402
import textwrap  # noqa: E402

probe_src = textwrap.dedent('''
    import importlib.util
    import os
    import sys

    ROOT = os.environ["ENGRAM_ROOT"]
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))

    from _smoke_common import install_stub
    install_stub()

    import hippocampus  # the previous load's cached copy
    stale = sys.modules["hippocampus"]
    stale.STALE_MARKER = True

    spec = importlib.util.spec_from_file_location(
        "data.plugins.astrbot_plugin_engram_core.main",
        os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    fresh = sys.modules.get("hippocampus")
    assert fresh is not None, "hippocampus missing after host-style load"
    assert fresh is not stale, "host-style load reused the cached package"
    assert not hasattr(fresh, "STALE_MARKER"), "cached module survived the reload"
    assert "hippocampus" in mod.EVICTED_ON_IMPORT, repr(mod.EVICTED_ON_IMPORT)
    print("RELOAD-OK " + ",".join(sorted(mod.EVICTED_ON_IMPORT)))
''')
probe = tempfile.NamedTemporaryFile(
    "w", suffix=".py", delete=False, encoding="utf-8")
probe.write(probe_src)
probe.close()
try:
    env = dict(os.environ, ENGRAM_ROOT=ROOT)
    r = subprocess.run([sys.executable, probe.name], capture_output=True,
                       text=True, env=env, timeout=180)
    check(r.returncode == 0 and "RELOAD-OK" in (r.stdout or ""),
          "host-style dotted reload reloads the bundles (rc=" + str(r.returncode)
          + ", out=" + ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-1][:120]
          if (r.stdout or r.stderr) else "")
finally:
    try:
        os.unlink(probe.name)
    except OSError:
        pass

print("TOTAL=" + str(len(CHECKS)) + " FAIL=" + str(len(FAILS)))
if FAILS:
    raise SystemExit(1)
