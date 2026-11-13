"""The switching state machine, with the per-model controller replaced by a fake."""
import json
import os
import unittest
from unittest import mock

from dwarfstar import service


class FakeControllers:
    """Stands in for `dwarfstar runtime --model ID ACTION` and records the calls."""

    def __init__(self, running=None, healthy=True, available_gib=60.0, start_error=None, stop_error=None, start_error_but_up=False):
        self.start_error_but_up = start_error_but_up
        self.running = set(running or [])
        self.healthy = healthy
        self.available_gib = available_gib
        self.start_error = start_error
        self.stop_error = stop_error
        self.calls = []

    def __call__(self, model_id, *arguments, timeout=None):
        action = arguments[0]
        self.calls.append((model_id, action))
        if action == "status":
            return {"ok": True, "running": model_id in self.running, "healthy": model_id in self.running and self.healthy,
                    "pid": 100 if model_id in self.running else None, "endpoint": "http://127.0.0.1:1/v1"}
        if action == "stop":
            if self.stop_error:
                return {"ok": False, "error": self.stop_error}
            self.running.discard(model_id)
            return {"ok": True, "stopped": 100}
        if action == "requirement":
            return {"ok": True, "required_gib": 52.09, "available_gib": self.available_gib}
        if action == "start":
            self.assert_exclusive(model_id)
            if self.start_error_but_up:
                self.running.add(model_id)
                return {"ok": False, "error": "controller interrupted"}
            if self.start_error:
                return {"ok": False, "error": self.start_error}
            self.running.add(model_id)
            return {"ok": True, "pid": 101, "load_seconds": 2.0}
        if action == "health":
            return {"ok": self.healthy}
        raise AssertionError(action)

    def assert_exclusive(self, model_id):
        assert not (self.running - {model_id}), "a second model would be loaded"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        patches = [mock.patch.object(service, "store_selection"), mock.patch.object(service, "store_active",
                   side_effect=lambda p: self.saved.__setitem__("active", p)), mock.patch.object(service, "lifecycle_log")]
        self.selection, self.active, _ = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def switch(self, fake, profile, lifecycle=None):
        with mock.patch.object(service, "controller", fake):
            result = (lifecycle or service.Lifecycle()).switch(profile)
        return result, self.active

    def stop(self, fake, lifecycle=None):
        with mock.patch.object(service, "controller", fake):
            return (lifecycle or service.Lifecycle()).stop_all()

    def test_start_from_idle(self):
        fake = FakeControllers()
        result, store = self.switch(fake, "scope-low")
        self.assertTrue(result["ok"])
        self.assertEqual(fake.running, {"scope"})
        store.assert_called_once_with("scope-low")
        self.selection.assert_called_once_with("scope-low")

    def test_switching_family_stops_the_other_model_first(self):
        fake = FakeControllers(running=["scope"])
        result, _ = self.switch(fake, "swift-medium")
        self.assertTrue(result["ok"])
        self.assertEqual(fake.running, {"swift"})
        self.assertLess(fake.calls.index(("scope", "stop")), fake.calls.index(("swift", "start")))

    def test_changing_effort_does_not_restart(self):
        fake = FakeControllers(running=["cyber"])
        result, store = self.switch(fake, "cyber-xhigh")
        self.assertTrue(result["ok"])
        self.assertNotIn(("cyber", "start"), fake.calls)
        self.assertNotIn(("cyber", "stop"), fake.calls)
        store.assert_called_once_with("cyber-xhigh")

    def test_refused_stop_leaves_everything_as_it_was(self):
        fake = FakeControllers(running=["scope"], stop_error="A client is connected. Finish the turn before stopping.")
        result, store = self.switch(fake, "cyber-low")
        self.assertFalse(result["ok"])
        self.assertIn("client is connected", result["error"])
        self.assertEqual(fake.running, {"scope"})
        self.assertNotIn(("cyber", "start"), fake.calls)
        self.assertNotIn(mock.call("cyber-low"), store.call_args_list)

    def test_failed_start_reports_the_controllers_message(self):
        message = "Cyber requires approximately 52.1 GiB available for the configured 64K context; 40.0 GiB is currently available."
        fake = FakeControllers(start_error=message)
        result, store = self.switch(fake, "cyber-low")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], message)
        self.assertEqual(fake.running, set())
        self.assertNotIn(mock.call("cyber-low"), store.call_args_list)

    def test_unhealthy_target_is_not_restarted(self):
        fake = FakeControllers(running=["swift"], healthy=False)
        result, _ = self.switch(fake, "swift-low")
        self.assertFalse(result["ok"])
        self.assertNotIn(("swift", "start"), fake.calls)
        self.assertNotIn(("swift", "stop"), fake.calls)

    def test_unknown_profile(self):
        result, _ = self.switch(FakeControllers(), "cyber-128k")
        self.assertFalse(result["ok"])

    def test_cyber_medium_stop_then_cyber_low_starts_with_low_active(self):
        """Regression for the reported failure: Cyber MEDIUM, Stop, select Cyber LOW, Start."""
        fake = FakeControllers()
        lifecycle = service.Lifecycle()
        first, _ = self.switch(fake, "cyber-medium", lifecycle)
        self.assertTrue(first["ok"])
        self.assertEqual(self.saved["active"], "cyber-medium")
        stopped = self.stop(fake, lifecycle)
        self.assertTrue(stopped["ok"])
        self.assertEqual(fake.running, set())                 # old process gone
        self.assertIsNone(self.saved["active"])               # no stale reasoning state
        self.assertFalse(lifecycle.current["busy"])           # controller idle
        second, _ = self.switch(fake, "cyber-low", lifecycle)
        self.assertTrue(second["ok"], second.get("error"))
        self.assertEqual(fake.running, {"cyber"})
        self.assertEqual(self.saved["active"], "cyber-low")
        self.assertEqual([c for c in fake.calls if c[1] == "start"], [("cyber", "start"), ("cyber", "start")])

    def test_a_start_reported_as_failed_keeps_a_healthy_model(self):
        fake = FakeControllers(start_error_but_up=True)
        result, store = self.switch(fake, "cyber-low")
        self.assertTrue(result["ok"])
        self.assertEqual(fake.running, {"cyber"})
        self.assertNotIn(("cyber", "stop"), fake.calls)
        store.assert_called_with("cyber-low")

    def test_stop_waits_until_the_process_is_gone(self):
        fake = FakeControllers(running=["cyber"])
        result = self.stop(fake)
        self.assertTrue(result["ok"])
        stop_index = fake.calls.index(("cyber", "stop"))
        self.assertIn(("cyber", "status"), fake.calls[stop_index + 1:])

    def test_one_job_at_a_time_and_identical_requests_join(self):
        import threading

        gate = threading.Event()
        fake = FakeControllers()
        original = fake.__call__

        def slow(model_id, *arguments, timeout=None):
            if arguments[0] == "start":
                gate.wait(5)
            return original(model_id, *arguments, timeout=timeout)

        lifecycle = service.Lifecycle()
        with mock.patch.object(service, "controller", slow):
            accepted = lifecycle.submit("switch", "cyber-low", wait=False)
            self.assertTrue(accepted["accepted"])
            joined = lifecycle.submit("switch", "cyber-low", wait=False)
            self.assertTrue(joined.get("joined"))
            refused = lifecycle.submit("switch", "scope-low", wait=False)
            self.assertFalse(refused["ok"])
            self.assertTrue(refused["busy"])
            self.assertEqual(refused["target"], "cyber-low")
            gate.set()
            outcome = lifecycle.wait(accepted["job"])
        self.assertTrue(outcome["ok"])
        self.assertFalse(lifecycle.current["busy"])

    def test_controller_output_parsing_ignores_stray_lines(self):
        self.assertEqual(service.parse_controller_output('warning: something\n{"ok": true, "pid": 3}\n'), {"ok": True, "pid": 3})
        self.assertIsNone(service.parse_controller_output("Traceback ...\nError"))

    def test_an_active_profile_for_a_model_that_is_not_running_is_ignored(self):
        with mock.patch.object(service, "active_profile", return_value="swift-medium"):
            self.assertIsNone(service.consistent_active(["cyber"]))
            self.assertEqual(service.consistent_active(["swift"]), "swift-medium")

    def test_state_computed_across_a_lifecycle_step_is_not_cached(self):
        """A status poll that started before a stop must not be served after the stop as current state."""
        svc = service.Service.__new__(service.Service)
        svc.state_cache, svc.state_generation, svc.lifecycle = None, 0, service.Lifecycle()
        calls = []

        def stale_then_fresh(lifecycle, detail=False):
            calls.append(1)
            if len(calls) == 1:
                svc.invalidate()  # the stop finishes while this poll is still collecting status
                return {"running_models": ["scope"], "lifecycle": {"busy": False}}
            return {"running_models": [], "lifecycle": {"busy": False}}

        with mock.patch.object(service, "state_document", stale_then_fresh):
            self.assertEqual(svc.state()["running_models"], ["scope"])  # that poll's own answer
            self.assertEqual(svc.state()["running_models"], [])         # but it was not cached
        self.assertEqual(len(calls), 2)

    def test_stop_succeeds_when_a_status_poll_already_retired_the_ledger(self):
        """The real race: the window's status poll retires the ledger of the exited server while stop() is finishing."""
        import tempfile
        from pathlib import Path

        os.environ.setdefault("DWARFSTAR_MODEL", "scope")
        from dwarfstar import runtime_control

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            row = {"pid": 4242, "birth": "b", "argv_sha256": "a", "command": str(runtime_control.SERVER) + " -u -P -m server.server"}
            (state / "server.json").write_text(json.dumps(row))
            alive = {"n": 2}

            def identity(pid):
                alive["n"] -= 1
                if alive["n"] <= 0:
                    (state / "server.json").unlink(missing_ok=True)  # the concurrent poll got there first
                    return None
                return dict(row)

            with mock.patch.object(runtime_control, "STATE", state), \
                 mock.patch.object(runtime_control, "identity", identity), \
                 mock.patch.object(runtime_control, "requests", return_value=[]), \
                 mock.patch.object(runtime_control, "run", return_value=mock.Mock(returncode=1, stdout="")), \
                 mock.patch.object(runtime_control, "occupied", return_value=False), \
                 mock.patch.object(runtime_control, "config", return_value={"port": 1}), \
                 mock.patch.object(runtime_control.os, "kill"), mock.patch.object(runtime_control, "emit"):
                result = runtime_control.stop()
            self.assertTrue(result["ok"])
            self.assertFalse((state / "server.json").exists())
            self.assertTrue((state / "last-stopped.json").exists())

    def test_fresh_state_selects_the_default(self):
        with mock.patch.object(service, "STATE_FILE", service.STATE_FILE.with_name("does-not-exist.json")):
            self.assertEqual(service.stored_selection(), "scope-low")


if __name__ == "__main__":
    unittest.main()
