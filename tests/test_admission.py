"""Per-model memory admission: each model is judged by its own plan, never one global figure."""
import os
import unittest
from unittest import mock

os.environ.setdefault("DWARFSTAR_MODEL", "scope")
from dwarfstar import runtime_control  # noqa: E402


class AdmissionTests(unittest.TestCase):
    def admit(self, model_id, available):
        with mock.patch.object(runtime_control, "MODEL", model_id), \
             mock.patch.object(runtime_control, "available_memory_gib", return_value=available):
            return runtime_control.admit_memory()

    def test_requirements_are_per_model(self):
        scope = runtime_control.memory_requirement("scope")["required_gib"]
        cyber = runtime_control.memory_requirement("cyber")["required_gib"]
        self.assertLess(scope, cyber)
        self.assertGreater(cyber, 45.0)  # the old single 45 GiB gate would have mis-judged Cyber

    def test_cyber_is_admitted_when_its_own_plan_fits(self):
        need = self.admit("cyber", 58.0)
        self.assertTrue(need["ok"])

    def test_refusal_names_the_model_the_numbers_and_what_to_do(self):
        with self.assertRaises(RuntimeError) as caught:
            self.admit("cyber", 40.0)
        message = str(caught.exception)
        self.assertIn("Cyber", message)
        required = runtime_control.memory_requirement("cyber")["required_gib"]
        self.assertIn(f"{required:.1f} GiB", message)
        self.assertIn("40.0 GiB", message)
        self.assertIn("Nothing was started", message)


if __name__ == "__main__":
    unittest.main()
