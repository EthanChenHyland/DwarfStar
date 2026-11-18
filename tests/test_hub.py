"""Telemetry hub reducers: runtime sessions, tool events and who is watching."""
import unittest

from dwarfstar.telemetry.hub import Hub


class HubTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(record=False)
        self.subscriber = self.hub.subscribe()

    def tearDown(self):
        self.hub.stop()

    def drain(self):
        events = []
        while not self.subscriber.queue.empty():
            events.append(self.subscriber.queue.get_nowait())
        return events

    def test_a_stopped_runtime_finalizes_the_session(self):
        self.hub.session_id = "20261112-000000-cyber"  # as if a session had been opened for a running model
        self.hub.ingest({"kind": "runtime", "src": "controller", "model": "cyber", "t": 1.0, "phase": "stopped"})
        self.assertIsNone(self.hub.snapshot()["hub"]["session"])

    def test_a_running_model_opens_one_session_and_a_stop_closes_it(self):
        hub = Hub(record=True)
        opened = []
        hub._open_session = lambda model: opened.append(model)
        hub.ingest({"kind": "runtime", "src": "controller", "model": "cyber", "t": 10.0, "phase": "starting"})
        self.assertEqual(opened, [])
        hub.ingest({"kind": "runtime", "src": "controller", "model": "cyber", "t": 40.0, "phase": "running", "pid": 7})
        self.assertEqual(opened, ["cyber"])
        hub.ingest({"kind": "runtime", "src": "controller", "model": "cyber", "t": 90.0, "phase": "stopped"})
        self.assertIn("cyber", hub.stopped_models)
        hub.stop()

    def test_the_snapshot_carries_only_what_live_producers_send(self):
        # Inference telemetry comes from the model server's /telemetry, not the hub; the native runtime's per-token
        # counters (mtp, ple, kv, rates, context, request) are retired.
        self.assertEqual(set(self.hub.snapshot()) - {"hub"}, {"model", "runtime", "tools", "agent"})
        self.assertNotIn("detail_level", self.hub.snapshot()["hub"])

    def test_tool_timeline(self):
        self.hub.ingest({"kind": "tool", "src": "agent", "t": 1.0, "phase": "start", "name": "bash", "category": "shell", "call": "abc"})
        self.assertEqual(self.hub.snapshot()["tools"]["active"]["name"], "bash")
        self.hub.ingest({"kind": "tool", "src": "agent", "t": 2.0, "phase": "end", "name": "bash", "category": "shell", "call": "abc", "ok": True, "ms": 812})
        tools = self.hub.snapshot()["tools"]
        self.assertIsNone(tools["active"])
        self.assertEqual(tools["count"], 1)
        self.assertEqual(tools["timeline"][0]["ms"], 812)

    def test_the_hub_is_watched_while_anyone_subscribes(self):
        hub = Hub(record=False)
        self.assertFalse(hub.watched())
        subscriber = hub.subscribe()
        self.assertTrue(hub.watched())
        hub.unsubscribe(subscriber)
        self.assertFalse(hub.watched())
        hub.stop()

    def test_every_subscriber_gets_every_event(self):
        other = self.hub.subscribe()
        self.hub.ingest({"kind": "tool", "src": "agent", "t": 1.0, "phase": "start", "name": "read", "category": "workspace", "call": "x"})
        self.assertEqual(self.drain()[0]["name"], "read")
        self.assertEqual(other.queue.get_nowait()["name"], "read")

if __name__ == "__main__":
    unittest.main()
