"""The catalog is the single source of truth: these tests pin its shape."""
import json
import unittest

from dwarfstar import catalog, instance, zed


class CatalogTests(unittest.TestCase):
    def test_profiles_are_model_times_effort_only(self):
        ids = [p["id"] for p in catalog.profiles()]
        self.assertEqual(ids, ["scope-low", "swift-low", "swift-medium", "cyber-off", "cyber-low", "cyber-medium", "cyber-xhigh"])
        for row in catalog.load()["profile_file"]["profiles"]:
            self.assertLessEqual(set(row), {"id", "model", "reasoning_effort", "policy"})
            self.assertNotIn("context", row)

    def test_no_profile_or_label_mentions_a_context_size(self):
        for profile in catalog.profiles():
            for token in ("32K", "64K", "128K"):
                self.assertNotIn(token, profile["label"])
                self.assertNotIn(token.lower(), profile["id"])

    def test_scope_is_the_only_default(self):
        self.assertEqual(catalog.default_profile()["id"], "scope-low")
        self.assertEqual([m["id"] for m in catalog.models().values() if m.get("default")], ["scope"])

    def test_context_is_a_model_property(self):
        self.assertEqual({m["id"]: m["context"] for m in catalog.models().values()},
                         {"scope": 131072, "swift": 131072, "cyber": 131072})

    def test_every_model_has_its_own_port_and_all_assets(self):
        ports = [m["port"] for m in catalog.models().values()]
        self.assertEqual(len(ports), len(set(ports)))
        for model in catalog.models().values():
            self.assertEqual(set(model["assets"]), set(catalog.ASSET_ROLES))
            for asset in model["assets"].values():
                self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
                self.assertFalse(asset["path"].startswith("/"), "asset paths are relative to the data root")

    def test_same_cache_policy_for_every_model(self):
        policies = {(m["cache"]["k_cache_dtype"], m["cache"]["v_cache_dtype"]) for m in catalog.models().values()}
        self.assertEqual(policies, {("INT8", "INT8")})  # the default backend's (Splash-m1) cache for every model

    def test_cyber_source_is_pinned_and_marked(self):
        source = catalog.model("cyber")["source"]
        self.assertEqual(source["repository"], "dealignai/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4")
        self.assertEqual(source["revision"], "15ef113c7090844f97f355f400169791dc76c2e3")
        self.assertEqual(source["policy"], "NO INTENTIONAL DE-ABLITERATION / BASE-WEIGHT RESTORATION")

    def test_tracked_config_has_no_machine_paths(self):
        for path in list((catalog.CONFIG / "models").glob("*.json")) + [catalog.CONFIG / "profiles.json", catalog.CONFIG / "runtimes.json"]:
            self.assertNotIn("/Users/", path.read_text(), path.name)


class GeneratedFilesTests(unittest.TestCase):
    def test_provider_pins_the_effort(self):
        text = instance.provider_yaml("swift-medium")
        self.assertIn("efforts: [medium]", text)
        self.assertIn("reasoning_effort: medium", text)
        self.assertIn("baseUrl: http://127.0.0.1:8031/v1", text)
        self.assertIn("contextWindow: 131072", text)

    def test_off_disables_thinking(self):
        text = instance.provider_yaml("cyber-off")
        self.assertIn("reasoning: false", text)
        self.assertIn("reasoning_effort: none", text)
        self.assertIn("chat_template_kwargs: {enable_thinking: false}", text)
        self.assertNotIn("efforts:", text)
        self.assertIn("contextWindow: 131072", text)

    def test_agent_registry_lists_only_the_models_profiles(self):
        registry = instance.agent_profiles("cyber")
        self.assertEqual(registry["active_profiles"], ["cyber-off", "cyber-low", "cyber-medium", "cyber-xhigh"])
        for profile_id, row in registry["profiles"].items():
            self.assertEqual(row["provider_config"], f"acp/providers/{profile_id}.yml")
            self.assertEqual(row["features"], {"recovery_enabled": False, "tool_result_hygiene_enabled": False, "finish_nudge_enabled": False})

    def test_every_profile_has_the_same_tool_policy(self):
        policies = {json.dumps({k: instance.agent_profiles(m)["profiles"][p][k] for k in ("policy", "prompt", "prompt_mode", "features")}, sort_keys=True)
                    for m in catalog.models() for p in instance.agent_profiles(m)["profiles"]}
        self.assertEqual(len(policies), 1, "model and effort are the only differences between profiles")

    def test_session_namespaces_keep_low_and_medium_apart(self):
        self.assertNotEqual(catalog.profile("swift-low")["state_namespace"], catalog.profile("swift-medium")["state_namespace"])


class EditorEntriesTests(unittest.TestCase):
    def test_one_entry_per_profile_through_one_launcher(self):
        entries = zed.entries()
        self.assertEqual(len(entries), 7)
        launchers = {value["command"] for value in entries.values()}
        self.assertEqual(len(launchers), 1)
        self.assertTrue(next(iter(launchers)).endswith("bin/dwarfstar-acp"))
        self.assertEqual(entries["DwarfStar Swift 1.5 MEDIUM"]["args"], ["--profile", "swift-medium"])
        self.assertEqual(entries["DwarfStar Cyber OFF"]["default_config_options"], {"thinking": "off"})

    def test_apply_replaces_only_dwarfstar_entries(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "settings.json"
            settings.write_text(json.dumps({"theme": "x", "agent_servers": {
                "Other Agent": {"command": "other"}, "DwarfStar Cyber LOW 64K": {"command": "old"}}}))
            with mock.patch.object(zed, "DATA", Path(folder)):
                result = zed.apply(settings)
            document = json.loads(settings.read_text())
            self.assertEqual(document["theme"], "x")
            self.assertEqual(document["agent_servers"]["Other Agent"], {"command": "other"})
            self.assertNotIn("DwarfStar Cyber LOW 64K", document["agent_servers"])
            self.assertEqual(result["removed"], ["DwarfStar Cyber LOW 64K"])
            self.assertTrue(zed.check(settings)["ok"])


class PolicyTests(unittest.TestCase):
    def test_only_justified_overrides_remain(self):
        overrides = {m: sorted(model["overrides"]) for m, model in catalog.models().items()}
        self.assertEqual(overrides, {"scope": [], "swift": [], "cyber": ["prefill_chunk"]})
        self.assertIn("Metal", catalog.model("cyber")["overrides"]["prefill_chunk"]["reason"])

    def test_every_model_gets_the_shared_policy(self):
        for model in catalog.models().values():
            self.assertEqual((model["context"], model["max_output"]), (131072, 81920))
            self.assertEqual((model["cache"]["k_cache_dtype"], model["cache"]["v_cache_dtype"]), ("INT8", "INT8"))

    def test_an_override_without_a_reason_is_rejected(self):
        defaults = catalog.policy()["model_defaults"]
        with self.assertRaises(catalog.CatalogError):
            catalog._apply_policy({"id": "x", "prefill_chunk": 512}, defaults)
        with self.assertRaises(catalog.CatalogError):
            catalog._apply_policy({"id": "x", "context": defaults["context"]}, defaults)
        with self.assertRaises(catalog.CatalogError):
            catalog._apply_policy({"id": "x", "override_reasons": {"context": "because"}}, defaults)

    def test_one_memory_formula_for_every_model(self):
        reserve = catalog.policy()["memory"]["system_reserve_gib"]
        for model_id, model in catalog.models().items():
            plan = catalog.memory_plan(model_id)
            memory = model["memory"]
            expected = memory["resident_model_gib"] + memory["kv_gib"] + memory["buffers_gib"] + memory["projector_gib"]
            self.assertAlmostEqual(plan["planned_gib"], expected, places=2)
            self.assertAlmostEqual(plan["required_gib"], expected + reserve, places=2)
            self.assertGreater(plan["metal_headroom_gib"], 0, f"{model_id} plan exceeds the Metal working set")


if __name__ == "__main__":
    unittest.main()


class BackendTests(unittest.TestCase):
    def test_splash_is_the_default_backend_for_every_model(self):
        for model in catalog.models().values():
            self.assertEqual((model["backend"]["kind"], model["backend"]["id"]), ("splash", "splash-m1-1.6.1-ds1"))

    def test_the_native_runtime_stays_selectable_as_the_rollback(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {"DWARFSTAR_RUNTIME_OVERRIDE": "scope=ds4-unified"}):
            catalog.load.cache_clear()
            try:
                scope = catalog.model("scope")
                self.assertEqual(scope["backend"]["kind"], "native")
                self.assertEqual((scope["cache"]["k_cache_dtype"], scope["cache"]["v_cache_dtype"]), ("F16", "F16"))
                self.assertAlmostEqual(catalog.memory_plan("scope")["planned_gib"], 43.73, places=2)
                from dwarfstar import instance

                self.assertTrue(instance.server_command("scope")[0][0].endswith("ds4-server"))
                self.assertEqual(catalog.model("swift")["backend"]["kind"], "splash")
            finally:
                catalog.load.cache_clear()

    def test_splash_serves_each_model_with_its_own_identity(self):
        from dwarfstar import instance

        scope = catalog.model("scope")
        self.assertEqual(scope["context"], 131072)
        self.assertEqual([p["id"] for p in catalog.profiles("scope")], ["scope-low"])
        self.assertLess(catalog.memory_plan("scope")["planned_gib"], 43.73)
        argv, env = instance.server_command("scope")
        joined = " ".join(argv)
        for flag in ("--served-model-name qwen3.8-flash-next", "--port 8030", "--max-context 131072", "--offline", "--mmproj", "--draft-model"):
            self.assertIn(flag, joined)
        self.assertIn("backends/splash-m1/home", env["HOME"])

    def test_swift_and_cyber_use_their_own_split_layout_on_splash(self):
        from unittest import mock

        from dwarfstar import instance

        with mock.patch.dict("os.environ", {"DWARFSTAR_RUNTIME_OVERRIDE": "swift=splash-m1-1.6.1-ds1,cyber=splash-m1-1.6.1-ds1"}):
            catalog.load.cache_clear()
            try:
                for model_id, stem in (("swift", "Swift1.5-GSQ-RCO-Q2_0-00001-of-00002.gguf"), ("cyber", "DwarfStarCyber-Q2_0-00001-of-00002.gguf")):
                    argv, _ = instance.server_command(model_id)
                    self.assertTrue(argv[argv.index("--model-dir") + 1].endswith("backends/splash-m1/artifacts/" + model_id + "/" + stem))
                    self.assertEqual(catalog.model(model_id)["context"], 131072)
                    self.assertLess(catalog.memory_plan(model_id)["planned_gib"], catalog.memory_plan(model_id)["metal_working_set_gib"])
                self.assertEqual(len(catalog.profiles("cyber")), 4)
            finally:
                catalog.load.cache_clear()

    def test_a_backend_without_a_block_is_refused(self):
        from unittest import mock

        with mock.patch.dict("os.environ", {"DWARFSTAR_RUNTIME_OVERRIDE": "cyber=splash-m1-1.6.1"}):
            catalog.load.cache_clear()
            try:
                with self.assertRaises(catalog.CatalogError):
                    catalog.load()
            finally:
                catalog.load.cache_clear()
