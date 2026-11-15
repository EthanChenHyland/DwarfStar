"""Local-service exposure and approval escalation: loopback-only API, static files confined to the web folder, every
request method guarded, Full Auto entered only by explicit confirmation, and a Workspace Auto agent unable to reach the
service or write the approval setting."""
import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import approval_policy, service, telemetry

ROOT = Path(__file__).resolve().parents[1]


class LoopbackRequestTests(unittest.TestCase):
    def test_loopback_hosts_and_origins_are_accepted(self):
        for host in ("127.0.0.1:8038", "localhost:8038", "127.0.0.1", "localhost"):
            self.assertTrue(service.loopback_request(host, None), host)
        for origin in ("http://127.0.0.1:8038", "http://localhost:8038", "https://localhost"):
            self.assertTrue(service.loopback_request("127.0.0.1:8038", origin), origin)

    def test_foreign_hosts_are_refused(self):
        # DNS rebinding: a page on evil.example resolves its own name to 127.0.0.1 and sends Host: evil.example.
        for host in (None, "", "evil.example:8038", "127.0.0.1.evil.example", "10.0.0.5:8038", "[::1]:8038",
                     "localhost.evil.example:8038", "0.0.0.0:8038"):
            self.assertFalse(service.loopback_request(host, None), host)

    def test_foreign_and_opaque_origins_are_refused(self):
        for origin in ("https://evil.example", "http://127.0.0.1.evil.example", "null", "file://", "chrome-extension://abc",
                       "http://localhost.evil.example:8038", "", "javascript:alert(1)"):
            self.assertFalse(service.loopback_request("127.0.0.1:8038", origin), origin)


class StaticFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.web = base / "web"
        (self.web / "assets").mkdir(parents=True)
        (self.web / "index.html").write_text("ui")
        (self.web / "assets" / "app.js").write_text("js")
        (base / "secret.json").write_text("{}")
        (self.web / "link").symlink_to(base / "secret.json")

    def test_files_in_the_web_folder_are_served(self):
        self.assertEqual(service.static_target("/", self.web), (self.web / "index.html").resolve())
        self.assertEqual(service.static_target("/assets/app.js", self.web), (self.web / "assets" / "app.js").resolve())

    def test_traversal_folders_and_links_out_are_refused(self):
        for path in ("/../secret.json", "/assets/../../secret.json", "//etc/passwd", "/link", "/assets", "/missing.js",
                     "/" + str(Path(self.folder.name) / "secret.json"), "/\x00"):
            self.assertIsNone(service.static_target(path, self.web), path)

    def test_the_real_web_folder_has_the_app(self):
        self.assertIsNotNone(service.static_target("/"))


class EveryMethodIsGuardedTests(unittest.TestCase):
    def test_each_request_method_checks_loopback_first(self):
        tree = ast.parse((ROOT / "src/dwarfstar/service.py").read_text())
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Handler")
        methods = [n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name.startswith("do_")]
        self.assertEqual(sorted(m.name for m in methods), ["do_GET", "do_POST"])  # a new method must be added here, guarded
        for method in methods:
            first = method.body[0]
            self.assertIsInstance(first, ast.If, method.name)
            self.assertIn("self._local_only()", ast.unparse(first.test), method.name)

    def test_the_server_binds_loopback_only(self):
        source = (ROOT / "src/dwarfstar/service.py").read_text()
        self.assertIn('ThreadingHTTPServer(("127.0.0.1", self.port), Handler)', source)
        self.assertNotIn("0.0.0.0", source)
        self.assertIn('"--host", "127.0.0.1"', (ROOT / "src/dwarfstar/instance.py").read_text())


class ApprovalEscalationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name) / "config" / "approval.json"
        patcher = mock.patch.object(approval_policy, "CONFIG", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("DWARFSTAR_APPROVAL_MODE", None)

    def test_a_fresh_installation_is_not_full_auto(self):
        self.assertFalse(self.config.exists())
        self.assertEqual(approval_policy.mode(), "safe_auto")

    def test_full_auto_needs_the_explicit_confirmation(self):
        for confirm in (None, "", "yes", True, "Full Auto"):
            with self.assertRaises(ValueError):
                approval_policy.set_mode("full_auto", confirm)
        self.assertFalse(self.config.exists())
        self.assertEqual(approval_policy.set_mode("full_auto", "full_auto")["approval_mode"], "full_auto")

    def test_the_choice_survives_a_restart_unchanged(self):
        approval_policy.set_mode("ask")
        self.assertEqual(approval_policy.mode(), "ask")  # read back from disk, as a new process would
        self.assertEqual(json.loads(self.config.read_text())["approval_mode"], "ask")

    def test_an_invalid_setting_fails_closed(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"schema": 1, "approval_mode": "yolo"}))
        with self.assertRaises(ValueError):
            approval_policy.mode()


@unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists(), "macOS seatbelt only")
class WorkspaceSandboxTests(unittest.TestCase):
    """A Workspace Auto agent runs under these seatbelt rules; check what its processes can reach."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "agent-tools"))
        import boundary

        self.boundary = boundary
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workspace = Path(self.folder.name) / "workspace"
        self.workspace.mkdir()
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.addCleanup(self.server.close)
        threading.Thread(target=self._answer, daemon=True).start()

    def _answer(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            connection.recv(1024)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            connection.close()

    def rules(self) -> str:
        with mock.patch.object(self.boundary, "mode", return_value="safe_auto"), \
                mock.patch.dict(os.environ, {"DWARFSTAR_HTTP_PORT": str(self.port)}):
            return self.boundary.tooling_policy(self.workspace)

    def sandboxed(self, rules: str, *command: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/usr/bin/sandbox-exec", "-p", rules, *command], capture_output=True, text=True, timeout=20)

    def curl(self, rules: str) -> str:
        return self.sandboxed(rules, "/usr/bin/curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5",
                              f"http://127.0.0.1:{self.port}/api/permissions").stdout

    def test_the_service_port_is_unreachable_from_the_sandbox(self):
        rules = self.rules()
        self.assertIn(f'(deny network-outbound (remote tcp "localhost:{self.port}"))', rules)
        self.assertIn(f'localhost:{telemetry.DEFAULT_HTTP_PORT}', rules)
        self.assertEqual(self.curl(rules), "000")
        # Control: the same rules without the service deny reach the port, so the refusal above is that rule's doing.
        opened = "\n".join(line for line in rules.splitlines() if f"localhost:{self.port}\"))" not in line or "deny" not in line)
        self.assertEqual(self.curl(opened), "200")

    def test_the_approval_setting_is_not_writable_from_the_sandbox(self):
        target = Path(self.folder.name) / "config" / "approval.json"
        target.parent.mkdir()
        result = self.sandboxed(self.rules(), "/usr/bin/touch", str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        inside = self.sandboxed(self.rules(), "/usr/bin/touch", str(self.workspace / "ok.txt"))
        self.assertEqual(inside.returncode, 0, inside.stderr)


if __name__ == "__main__":
    unittest.main()
