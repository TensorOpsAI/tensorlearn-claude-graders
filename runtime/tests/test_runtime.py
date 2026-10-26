import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Broker, InfrastructureError, PolicyError, ROOT, bounded_process, clean_env, load_profile, permitted, verify_release
from launch import configuration, prepare
from server import dispatch, tools_for


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.profile = {"writeAllow": ["answer.json", "src/**"], "writeDeny": ["src/protected.mjs"], "tests": {"practice": ["node", "practice.mjs"]}, "coach": "Practice"}
        self.broker = Broker(self.workspace, self.profile)

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_replacement(self):
        self.broker.apply_patch("answer.json", '{"answer":2}\n')
        self.assertEqual((self.workspace / "answer.json").read_text(), '{"answer":2}\n')
        self.broker.apply_patch("answer.json", '{"answer":3}\n')
        self.assertEqual((self.workspace / "answer.json").read_text(), '{"answer":3}\n')

    def test_nested_write(self):
        self.broker.apply_patch("src/nested/a.mjs", "export default 1;\n")
        self.assertTrue((self.workspace / "src/nested/a.mjs").is_file())

    def test_forbidden_paths(self):
        for path in ["/tmp/x", "../x", "src/../x", "src//x", "src/./x", "src\\x", "src/\x00x", "src/\nx"]:
            with self.subTest(path=path), self.assertRaises(PolicyError):
                self.broker.apply_patch(path, "x")

    def test_deny_priority(self):
        with self.assertRaises(PolicyError):
            self.broker.apply_patch("src/protected.mjs", "x")

    def test_scope_infra(self):
        broad = {**self.profile, "writeAllow": ["**"]}
        for path in [".git/config", ".github/workflows/grade.yml", ".mcp.json", ".claude/settings.json", "exercise.json", "runtime/server.py"]:
            self.assertFalse(permitted(path, broad), path)
        self.assertTrue(permitted("CLAUDE.md", broad))
        self.assertTrue(permitted("src/main.mjs", broad))

    def test_protected_case_alias(self):
        broad = {**self.profile, "writeAllow": ["**"]}
        self.assertFalse(permitted(".GIT/config", broad))
        self.assertFalse(permitted(".CLAUDE/SETTINGS.JSON", broad))

    def test_glob_segments(self):
        profile = {**self.profile, "writeAllow": ["*.md", "src/**"]}
        self.assertTrue(permitted("notes.md", profile))
        self.assertFalse(permitted("nested/notes.md", profile))
        self.assertTrue(permitted("src/nested/code.mjs", profile))

    def test_file_symlink(self):
        target = self.base / "outside"
        target.write_text("original")
        (self.workspace / "answer.json").symlink_to(target)
        with self.assertRaises(PolicyError):
            self.broker.apply_patch("answer.json", "changed")
        self.assertEqual(target.read_text(), "original")

    def test_directory_symlink(self):
        (self.workspace / "src").symlink_to(self.base)
        with self.assertRaises(PolicyError):
            self.broker.apply_patch("src/outside", "changed")
        self.assertFalse((self.base / "outside").exists())

    def test_hardlink_replaced_without_editing_other_inode(self):
        target = self.base / "outside"
        target.write_text("original")
        os.link(target, self.workspace / "answer.json")
        self.broker.apply_patch("answer.json", "changed")
        self.assertEqual(target.read_text(), "original")

    def test_size_limit(self):
        with self.assertRaises(PolicyError):
            self.broker.apply_patch("answer.json", "x" * 65537)

    def test_snapshot_has_no_symlinks(self):
        (self.workspace / "link").symlink_to(self.base)
        with self.assertRaises(PolicyError):
            self.broker._snapshot(self.base / "snapshot")

    def test_snapshot_excludes_git(self):
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git/config").write_text("secret")
        (self.workspace / "answer.json").write_text("{}")
        target = self.base / "snapshot"
        self.broker._snapshot(target)
        self.assertFalse((target / ".git").exists())
        self.assertEqual((target / "answer.json").read_text(), "{}")

    def test_snapshot_rejects_hardlinks(self):
        target = self.base / "outside"
        target.write_text("private")
        os.link(target, self.workspace / "answer.json")
        with self.assertRaises(PolicyError):
            self.broker._snapshot(self.base / "snapshot")

    def test_no_arbitrary_commands(self):
        for test in ["node practice.mjs", "practice;env", "../../run", "other"]:
            with self.assertRaises(PolicyError):
                self.broker.run_tests(test)

    def test_missing_image_fails(self):
        with self.assertRaises(InfrastructureError):
            self.broker.run_tests("practice")

    def test_unpinned_image_fails(self):
        self.broker.image = "node:22"
        with self.assertRaises(InfrastructureError):
            self.broker.run_tests("practice")

    def test_environment_drops_secrets(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "GITHUB_TOKEN": "secret", "DOCKER_HOST": "remote", "NODE_OPTIONS": "bad"}):
            self.assertEqual(set(clean_env()), {"PATH", "LANG"})

    def test_worker_docker_contract(self):
        self.broker.image = "node@sha256:" + "a" * 64
        completed = subprocess.CompletedProcess([], 0)
        with patch("core.subprocess.run", return_value=completed), patch("core.bounded_process", return_value={"exitCode": 0, "passed": True, "output": "ok"}) as execute:
            result = self.broker.run_tests("practice")
        command = execute.call_args.args[0]
        for flag in ["--network=none", "--read-only", "--cap-drop=ALL", "--user=65534:65534", "--pull=never"]:
            self.assertIn(flag, command)
        self.assertFalse(result["officialScore"])
        self.assertEqual(command[-2:], ["node", "practice.mjs"])

    def test_docker_unavailable_fails(self):
        self.broker.image = "node@sha256:" + "a" * 64
        with patch("core.subprocess.run", side_effect=FileNotFoundError), self.assertRaises(InfrastructureError):
            self.broker.run_tests("practice")

    def test_intent_empty_inventory_and_no_call(self):
        broker = Broker(self.workspace, load_profile("intent"))
        self.assertEqual(tools_for(broker), [])
        response = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "apply_patch", "arguments": {"path": "intent.md", "content": "draft"}}}, broker)
        self.assertTrue(response["result"]["isError"])
        self.assertFalse((self.workspace / "intent.md").exists())

    def test_mcp_initialization_and_ping(self):
        for method in ["initialize", "ping", "tools/list"]:
            response = dispatch({"jsonrpc": "2.0", "id": 3, "method": method}, self.broker)
            self.assertEqual(response["id"], 3)
            self.assertIn("result", response)

    def test_unknown_tool_and_extra_argument(self):
        for name, arguments in [("Bash", {"command": "env"}), ("apply_patch", {"path": "answer.json", "content": "2", "shell": "env"})]:
            response = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, self.broker)
            self.assertTrue(response["result"]["isError"])

    def test_release_policy_tampering_detected(self):
        target = self.base / "runtime"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__"))
        (target / "profiles.json").write_text('{}')
        with self.assertRaises(PolicyError):
            verify_release(target)

    def test_all_profile_shapes(self):
        profiles = json.loads((ROOT / "profiles.json").read_text())
        self.assertEqual(len(profiles), 13)
        for name in profiles:
            load_profile(name)

    def test_learner_manifest_is_ignored(self):
        (self.workspace / "exercise.json").write_text('{"writeAllow":["**"]}')
        broker = Broker(self.workspace, load_profile("preflight"))
        with self.assertRaises(PolicyError):
            broker.apply_patch("arbitrary.mjs", "x")

    def test_launch_intent_has_empty_mcp(self):
        configdir = self.base / "config"
        configdir.mkdir()
        model = "claude-test-1-20260101"
        command, tools = prepare(self.workspace, "intent", model, {"models": [model], "workerImage": None}, configdir, "/bin/claude")
        self.assertEqual(tools, [])
        self.assertEqual(json.loads((configdir / "mcp.json").read_text()), {"mcpServers": {}})
        for flag in ["--restricted", "--strict-mcp-config", "--no-chrome"]:
            self.assertIn(flag, command)
        for flag in ["--resume", "--continue", "--bare", "--safe-mode", "--dangerously-skip-permissions"]:
            self.assertNotIn(flag, command)

    def test_generated_config_cannot_live_in_workspace(self):
        with self.assertRaises(PolicyError):
            prepare(self.workspace, "intent", "model", {"models": ["model"], "workerImage": None}, self.workspace, "claude")

    def test_model_alias_rejected(self):
        path = self.base / "config.json"
        path.write_text(json.dumps({"models": ["sonnet"], "workerImage": None}))
        with self.assertRaises(PolicyError):
            configuration(path)

    def test_versioned_model_id(self):
        path = self.base / "config.json"
        path.write_text(json.dumps({"models": ["claude-sonnet-4-6"], "workerImage": None}))
        self.assertEqual(configuration(path)["models"], ["claude-sonnet-4-6"])

    def test_model_allowlist_enforced(self):
        destination = self.base / "config"
        destination.mkdir()
        with self.assertRaises(PolicyError):
            prepare(self.workspace, "intent", "other", {"models": ["allowed"], "workerImage": None}, destination, "claude")

    def test_bounded_output(self):
        with self.assertRaises(InfrastructureError):
            bounded_process([sys.executable, "-c", "print('x'*70000)"], 5)

    def test_timeout(self):
        with self.assertRaises(InfrastructureError):
            bounded_process([sys.executable, "-c", "import time; time.sleep(2)"], .05)

    def test_process_result(self):
        result = bounded_process([sys.executable, "-c", "print('ok')"], 5)
        self.assertTrue(result["passed"])
        self.assertEqual(result["output"], "ok\n")


if __name__ == "__main__":
    unittest.main()
