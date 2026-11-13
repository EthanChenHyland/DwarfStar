"""Tool-result redaction and approval policy."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent-tools"))
from policy import redaction  # noqa: E402

from dwarfstar import approval_policy  # noqa: E402


class RedactionTests(unittest.TestCase):
    def test_workspace_secrets_and_their_encodings_are_removed(self):
        import base64

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".env").write_text("API_KEY=abcdef1234567890XYZ\n")
            values = redaction.secret_values(folder)
            text = "plain abcdef1234567890XYZ and encoded " + base64.b64encode(b"abcdef1234567890XYZ").decode()
            clean = redaction.redact(text, values)
            self.assertNotIn("abcdef1234567890XYZ", clean)
            self.assertNotIn(base64.b64encode(b"abcdef1234567890XYZ").decode(), clean)

    def test_private_keys_and_sensitive_json_keys(self):
        value = {"password": "hunter2hunter2", "note": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"}
        clean = redaction.redact_obj(value, set())
        self.assertEqual(clean["password"], "[REDACTED]")
        self.assertEqual(clean["note"], "[REDACTED PRIVATE KEY]")


class ApprovalTests(unittest.TestCase):
    MESSAGE = {"jsonrpc": "2.0", "id": 7, "method": "session/request_permission",
               "params": {"sessionId": "s", "toolCall": {"toolCallId": "t"},
                          "options": [{"optionId": "a", "name": "Allow", "kind": "allow_once"}, {"optionId": "r", "name": "Reject", "kind": "reject_once"}]}}

    def test_full_auto_approves_without_waiting(self):
        answer = approval_policy.resolve_request(self.MESSAGE, "full_auto")
        self.assertEqual(answer["result"]["outcome"], {"outcome": "selected", "optionId": "a"})

    def test_other_modes_leave_the_decision_to_the_user(self):
        self.assertIsNone(approval_policy.resolve_request(self.MESSAGE, "ask"))

    def test_malformed_requests_are_rejected(self):
        broken = json.loads(json.dumps(self.MESSAGE))
        broken["params"]["options"] = []
        with self.assertRaises(ValueError):
            approval_policy.resolve_request(broken, "full_auto")


if __name__ == "__main__":
    unittest.main()
