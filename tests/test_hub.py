"""Telemetry hub reducers: runtime sessions, recorded verify cycles and tool events."""
import unittest

from dwarfstar.telemetry.hub import Hub


class HubTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(record=False)
        self.subscriber = self.hub.subscribe(2)

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

    def test_ticks_accumulate_mtp_ple_and_router_heat(self):
        row = [3, 128, 7, 64]
        for index in range(4):
            self.hub.ingest({"kind": "tick", "src": "native", "model": "cyber", "t": 2.0 + index, "seq": index, "pos": 100 + 2 * index,
                             "n": 2, "dp": 1, "da": 1 if index < 3 else 0, "ms": 50.0, "ple": [10, 900], "k": 2, "r": [row, row]})
        live = self.hub.snapshot()
        self.assertEqual(live["mtp"]["cycles"], 4)
        self.assertEqual(live["mtp"]["proposed"], 4)
        self.assertEqual(live["mtp"]["accepted"], 3)
        self.assertEqual(live["mtp"]["rejected"], 1)
        self.assertEqual(live["mtp"]["acceptance_pct"], 75.0)
        self.assertEqual(live["ple"]["reads"], 40)
        self.assertEqual(live["ple"]["bytes"], 3600)
        heat = self.hub.heat("cyber")["layers"]
        self.assertEqual(heat[0][3], 4)
        self.assertEqual(heat[1][7], 4)
        self.assertEqual(sum(heat[0]), 8)

    def test_router_rows_only_reach_detail_subscribers(self):
        basic = self.hub.subscribe(1)
        self.hub.ingest({"kind": "tick", "src": "native", "model": "cyber", "t": 5.0, "seq": 0, "pos": 1, "n": 1, "dp": 0, "da": 0,
                         "ms": 30.0, "ple": [1, 90], "k": 1, "r": [[5, 255]]})
        self.hub._flush_tick(force=True)
        self.assertIn("r", self.subscriber.queue.get_nowait())
        self.assertNotIn("r", basic.queue.get_nowait())

    def test_tool_timeline(self):
        self.hub.ingest({"kind": "tool", "src": "agent", "t": 1.0, "phase": "start", "name": "bash", "category": "shell", "call": "abc"})
        self.assertEqual(self.hub.snapshot()["tools"]["active"]["name"], "bash")
        self.hub.ingest({"kind": "tool", "src": "agent", "t": 2.0, "phase": "end", "name": "bash", "category": "shell", "call": "abc", "ok": True, "ms": 812})
        tools = self.hub.snapshot()["tools"]
        self.assertIsNone(tools["active"])
        self.assertEqual(tools["count"], 1)
        self.assertEqual(tools["timeline"][0]["ms"], 812)

    def test_subscribers_set_the_detail_level(self):
        hub = Hub(record=False)
        self.assertEqual(hub.wanted_level(), 0)
        subscriber = hub.subscribe(2)
        self.assertEqual(hub.wanted_level(), 2)
        hub.unsubscribe(subscriber)
        self.assertEqual(hub.wanted_level(), 0)
        hub.stop()


if __name__ == "__main__":
    unittest.main()
