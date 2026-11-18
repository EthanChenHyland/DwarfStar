"""ds3 GPU telemetry: the catalog's capability mapping and the engine-contract checker (scripts/backends/ds3_check.py).

The checker runs against a live ds3 server in the experiment (docs/DS3-GPU-TELEMETRY.md); here it is driven with small
synthetic batches shaped like the engine's, so each invariant is shown to pass a valid run and to catch its violation.
"""
import copy
import importlib.util
import unittest
from pathlib import Path

from dwarfstar import catalog

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ds3_check", ROOT / "scripts/backends/ds3_check.py")
ds3_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds3_check)

LAYERS, EXPERTS, TOP_K = 3, 8, 2


def _aggregate(seq, phase, steps, per_layer_cells):
    layers = [[x for cell in cells for x in cell] for cells in per_layer_cells]
    return {"seq": seq, "t": seq, "kind": "router_aggregate", "phase": phase, "steps": steps,
            "routes": sum(count for cells in per_layer_cells for _, count in cells), "cells": sum(len(c) for c in per_layer_cells),
            "interval_ms": 200, "payload": True, "layers": layers}


def _run():
    """Two decode cycles of one lane: 2 drafted + 1 accepted-or-not → 3 rows, then 1 drafted → 2 rows: 5 rows routed
    TOP_K experts in each of LAYERS layers, so 10 routes per layer; one prefill step of 2 tokens; one sample."""
    cycles = [
        {"seq": 1, "t": 1, "kind": "decode_cycle", "width": 1, "output": 2, "drafted": 2, "accepted": 1, "wall_ms": 70, "cycle_ms": 71},
        {"seq": 2, "t": 2, "kind": "decode_cycle", "width": 1, "output": 2, "drafted": 1, "accepted": 1, "wall_ms": 70, "cycle_ms": 71},
    ]
    decode = _aggregate(3, "decode", 2, [[(0, 6), (5, 4)], [(1, 5), (2, 5)], [(7, 6), (0, 4)]])
    prefill = _aggregate(4, "prefill", 1, [[(0, 4)], [(3, 2), (4, 2)], [(6, 4)]])
    sample = {"seq": 5, "t": 5, "kind": "router_sample", "step": 8, "every": 8, "top_k": TOP_K, "layers": [[0, 5], [1, 2], [7, 0]]}
    ple = {"seq": 6, "t": 6, "kind": "ple_aggregate", "interval_ms": 500, "payload": True,
           "table": {"requested_rows": 40, "hit_rows": 30, "read_rows": 10, "read_bytes": 900, "cached_rows": 10}}
    header = {"available": True, "layers": LAYERS, "experts": EXPERTS, "top_k": TOP_K, "mode": "sampled", "samples_dropped": 0}
    batches = [{"schema": 2, "next": 3, "dropped": 0, "router": header, "events": cycles},
               {"schema": 2, "next": 7, "dropped": 0, "router": header, "events": [decode, prefill, sample, ple]}]
    before = {"streamed_table": {"cached_rows": 100, "read_bytes": 5000, "evictions": 0}}
    after = {"streamed_table": {"cached_rows": 110, "read_bytes": 5900, "evictions": 0}}
    return batches, before, after


class CapabilityTests(unittest.TestCase):
    def test_ds2_reports_no_router_or_row_aggregates(self):
        runtimes = catalog.load()["runtimes"]
        for model in catalog.models().values():
            telemetry = catalog._backend({**model, "runtime": "splash-m1-1.6.1-ds2-telemetry"}, runtimes)["telemetry"]
            self.assertEqual(telemetry["routing"], "unavailable")
            self.assertEqual(telemetry["routing_levels"], [])
            self.assertEqual(telemetry["ple"], "none")
            self.assertEqual(telemetry["dflash"], "cycle")

    def test_capabilities_are_exactly_the_reported_semantics(self):
        # No native-era flags (router/ple/mtp rows) and no constants (requests/memory_plan): only what /telemetry reports.
        for model in catalog.models().values():
            self.assertEqual(set(model["backend"]["telemetry"]), {"dflash", "routing", "routing_levels", "ple"})
        ds1 = catalog._backend({"runtime": "splash-m1-1.6.1-ds1", "splash_model_name": "x"}, catalog.load()["runtimes"])["telemetry"]
        self.assertEqual((ds1["dflash"], ds1["routing"], ds1["ple"]), ("none", "unavailable", "none"))

    def test_ds3_maps_router_levels_and_never_offers_exact(self):
        model = {"runtime": "ds3", "splash_model_name": "x"}
        runtimes = {"runtimes": {"ds3": {"kind": "splash", "advanced_telemetry": {
            "dflash": "cycle", "routing": "sampled", "routing_levels": ["aggregate", "sampled", "exact"], "ple": "aggregate"}}}}
        telemetry = catalog._backend(model, runtimes)["telemetry"]
        self.assertEqual((telemetry["routing"], telemetry["routing_levels"], telemetry["ple"]),
                         ("sampled", ["aggregate", "sampled"], "aggregate"))
        runtimes["runtimes"]["ds3"]["advanced_telemetry"]["routing"] = "exact"
        self.assertEqual(catalog._backend(model, runtimes)["telemetry"]["routing"], "unavailable")


class PinTests(unittest.TestCase):
    """The rollback switch is config/runtime-policy.json: whichever runtime it names must resolve to its own capabilities,
    and an installed runtime must be byte-identical to its pin, so flipping the policy loads exactly the reviewed build."""

    def test_each_pin_resolves_its_own_capabilities(self):
        runtimes = catalog.load()["runtimes"]
        model = {"splash_model_name": "x"}
        ds2 = catalog._backend({**model, "runtime": "splash-m1-1.6.1-ds2-telemetry"}, runtimes)["telemetry"]
        ds3 = catalog._backend({**model, "runtime": "splash-m1-1.6.1-ds3-gpu-telemetry"}, runtimes)["telemetry"]
        self.assertEqual((ds2["dflash"], ds2["routing"], ds2["ple"]), ("cycle", "unavailable", "none"))
        self.assertEqual((ds3["dflash"], ds3["routing"], ds3["routing_levels"], ds3["ple"]),
                         ("cycle", "sampled", ["aggregate", "sampled"], "aggregate"))

    def test_installed_runtimes_match_their_pins(self):
        import hashlib
        from dwarfstar.paths import ASSETS

        for name, pin in catalog.load()["runtimes"]["runtimes"].items():
            root = ASSETS / pin["directory"]
            if not (root / "engine/splash").is_file():
                continue  # not installed on this machine
            digest = lambda path: hashlib.sha256((root / path).read_bytes()).hexdigest()
            self.assertEqual(digest("engine/splash"), pin["binary_sha256"], name)
            for shader, sha in pin["shader_sha256"].items():
                self.assertEqual(digest("engine/" + shader), sha, f"{name} {shader}")
            for adapter, sha in pin.get("adapter_sha256", {}).items():
                self.assertEqual(digest(adapter), sha, f"{name} {adapter}")


class CheckerTests(unittest.TestCase):
    def test_a_valid_run_passes(self):
        report = ds3_check.check(*_run())
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["router"]["verify_rows"], 5)
        self.assertEqual(report["router"]["steps"], {"decode": 2, "prefill": 1})

    def _fails(self, mutate, needle):
        batches, before, after = _run()
        mutate(batches, before, after)
        problems = ds3_check.check(batches, before, after)["problems"]
        self.assertTrue(any(needle in p for p in problems), f"expected {needle!r} in {problems}")

    def test_schema(self):
        self._fails(lambda b, *_: b[1].update(schema=1), "schema")

    def test_sequence_monotonicity(self):
        self._fails(lambda b, *_: b[1]["events"][0].update(seq=1), "strictly increase")

    def test_next_never_goes_back(self):
        self._fails(lambda b, *_: b[1].update(next=2), "next went backwards")

    def test_bounds(self):
        self._fails(lambda b, *_: b[1]["events"][0]["layers"][0].extend([EXPERTS, 1]), "bad cell")
        self._fails(lambda b, *_: b[1]["events"][0]["layers"].append([0, 1]), "layers >")
        self._fails(lambda b, *_: b[1]["router"].update(layers=65), "out of bounds")

    def test_routes_match_verify_rows(self):
        def extra(batches, *_):
            batches[0]["events"][0]["drafted"] = 3
        self._fails(extra, "verify rows")

    def test_steps_match_cycles(self):
        self._fails(lambda b, *_: b[1]["events"][0].update(steps=3), "decode cycles")

    def test_per_layer_totals_equal(self):
        def skew(batches, *_):
            agg = batches[1]["events"][0]
            agg["layers"][2] = [7, 9]
            agg["routes"] -= 1
        self._fails(skew, "per-layer route totals differ")

    def test_samples_belong_to_the_aggregate_and_their_period(self):
        self._fails(lambda b, *_: b[1]["events"][2]["layers"].__setitem__(0, [0, 3]), "never counted")
        self._fails(lambda b, *_: b[1]["events"][2].update(step=9), "multiple of its period")
        self._fails(lambda b, *_: b[1]["events"][2]["layers"].__setitem__(1, [1, 1]), "bad ids")

    def test_dropped_samples_are_reported(self):
        self._fails(lambda b, *_: b[1]["router"].update(samples_dropped=2), "samples dropped")

    def test_ple_matches_status(self):
        self._fails(lambda b, before, after: after["streamed_table"].update(read_bytes=6000), "PLE read bytes")

    def test_payload_less_intervals_are_counted_not_checked_cellwise(self):
        batches, before, after = _run()
        summary = copy.deepcopy(batches[1]["events"][1])
        summary.update(seq=7, payload=False, layers=None, steps=0, routes=0)
        batches[1]["events"].append(summary)
        report = ds3_check.check(batches, before, after)
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["other_consumer_events"], 1)

    def test_expired_detail_skips_payload_checks_but_keeps_the_totals(self):
        batches, before, after = _run()
        sample = batches[1]["events"][2]
        sample.update(payload=False, layers=[])
        batches[1]["events"][3]["table"]["read_bytes"] = 100  # the rest arrived without detail
        report = ds3_check.check(batches, before, after)
        self.assertEqual(report["problems"], [])
        self.assertIn("without detail", report["note"])
        batches[1]["events"][0]["steps"] = 5  # step and route totals are still checked
        self.assertTrue(ds3_check.check(batches, before, after)["problems"])


if __name__ == "__main__":
    unittest.main()
