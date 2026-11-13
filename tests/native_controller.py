"""The controller as it runs the native runtime, for tests of the native ownership ledger.

runtime_control reads its backend once at import; Splash-m1 is the default, so tests that exercise the native ledger
(a server identified by its exact ds4-server command line, orphan adoption) patch the native constants in."""
from contextlib import ExitStack
from unittest import mock

from dwarfstar import catalog
from dwarfstar.paths import DATA


def native_controller(runtime_control) -> ExitStack:
    directory = DATA / catalog.load()["runtimes"]["runtimes"]["ds4-unified"]["directory"]
    stack = ExitStack()
    for name, value in (("KIND", "native"), ("RUNTIME_DIR", directory), ("SERVER", directory / "ds4-server")):
        stack.enter_context(mock.patch.object(runtime_control, name, value))
    return stack
