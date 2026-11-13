"""Telemetry hub reducers: native progress records, verify cycles and tool events."""
import unittest

from dwarfstar.telemetry.hub import Hub


def native(line):
    return {"kind": "log", "src": "native", "model": "cyber", "t": 1.0, "type": "generation", "line": "ds4-server: " + line}


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

    def test_a_late_native_record_does_not_reopen_a_stopped_session(self):
        hub = Hub(record=True)
        opened = []
        hub._open_session = lambda model: opened.append(model)
        hub.ingest({"kind": "runtime", "src": "controller", "model": "cyber", "t": 10.0, "phase": "stopped"})
        late = native("chat ctx=0..41:41 prompt start")
        late["t"] = 9.5  # written by the runtime before it exited, delivered after the stop
        hub.ingest(late)
        self.assertEqual(opened, [])
        fresh = native("chat ctx=0..41:41 prompt start")
        fresh["t"] = 11.0
        hub.ingest(fresh)
        self.assertEqual(opened, ["cyber"])
        hub.stop()

    def test_request_lifecycle_from_native_records(self):
        for line in ("chat ctx=0..8329:8329 TOOLS prompt start",
                     "chat ctx=0..8329:8329 TOOLS prefill chunk 4096/8329 (49.2%) chunk=301.00 t/s avg=300.50 t/s 13.630s",
                     "chat ctx=0..8329:8329 TOOLS prompt done 27.700s",
                     "chat ctx=8329..8379:50 gen=50 TOOLS THINKING decoding chunk=29.40 t/s avg=29.40 t/s 1.701s",
                     "tool calls ctx=0..8329:8329 n=1 raw_tool_text=1 ids=[call_x] names=[read]",
                     "chat ctx=0..8329:8329 gen=119 TOOLS DSML_START DSML_END finish=tool_calls 31.121s"):
            self.hub.ingest(native(line))
        phases = [e["phase"] for e in self.drain() if e["kind"] == "request"]
        self.assertEqual(phases, ["start", "prefill", "prefill_done", "decode", "tool_calls", "finish"])
        live = self.hub.snapshot()
        self.assertEqual(live["request"]["prompt_tokens"], 8329)
        self.assertEqual(live["request"]["generated"], 119)
        self.assertEqual(live["request"]["finish"], "tool_calls")
        self.assertFalse(live["request"]["active"])
        self.assertAlmostEqual(live["rates"]["decode_tps"], 29.4)
        self.assertAlmostEqual(live["rates"]["prefill_tps"], 8329 / 27.7, places=1)
        self.assertEqual(live["context"]["used"], 8329 + 119)

    def test_text_and_paths_never_become_events(self):
        self.hub.ingest(native("kv cache hit text tokens=7979 text=34264 quant=2 key=token-text load=59.8 ms"))
        events = self.drain()
        self.assertEqual([(e["kind"], e["phase"], e["tokens"]) for e in events], [("kv", "hit", 7979)])
        for event in events:
            self.assertNotIn("line", event)

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

    def test_no_subscriber_means_no_detail_requested(self):
        hub = Hub(record=False)
        self.assertEqual(hub.wanted_level(), 0)
        subscriber = hub.subscribe(2)
        self.assertEqual(hub.wanted_level(), 2)
        hub.unsubscribe(subscriber)
        self.assertEqual(hub.wanted_level(), 0)
        hub.stop()


if __name__ == "__main__":
    unittest.main()
