import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Broker, InfrastructureError, PolicyError
from replay import TASKS, parse_events, main


class ReplayTests(unittest.TestCase):
    def test_prompts_do_not_reveal_diagnosis(self):
        for task in TASKS.values():
            self.assertNotIn("CLAUDE.md", task)
            self.assertNotIn("memory", task.lower())

    def test_stream_requires_success_result(self):
        for raw in ['', '{}\n', '{"type":"result","subtype":"error"}\n', 'not json']:
            with self.assertRaises(InfrastructureError):
                parse_events(raw)

    def test_stream_preserves_events(self):
        raw = '{"type":"system"}\n{"type":"result","subtype":"success","is_error":false}\n'
        self.assertEqual(len(parse_events(raw)), 2)

    def test_unknown_controller_check_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = Broker(directory, {"writeAllow": [], "writeDeny": [], "tests": {}, "coach": ""})
            with self.assertRaises(PolicyError):
                broker.run_trusted_check("node -e process.env")

    def test_two_fresh_copies_with_mocked_claude(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "exercise"
            workspace.mkdir()
            (workspace / "CLAUDE.md").write_text("project guidance")
            config = root / "config.json"
            config.write_text(json.dumps({"models": ["claude-sonnet-4-6"], "workerImage": "node@sha256:" + "a"*64}))
            output = root / "observations"
            args = ['replay.py', '--workspace', str(workspace), '--configuration', str(config), '--model', 'claude-sonnet-4-6', '--output', str(output)]
            calls = []
            def transport(command, prompt, cwd, **kwargs):
                calls.append((command, prompt, cwd))
                self.assertEqual((cwd / 'CLAUDE.md').read_text(), 'project guidance')
                (cwd / 'task-result.md').write_text('local change')
                return '{"type":"result","subtype":"success","is_error":false}\n'
            with patch.object(sys, 'argv', args), patch('replay.check_cli'), patch('replay.shutil.which', return_value='/fake/claude'), patch('replay.run_claude', side_effect=transport), patch.object(Broker, 'run_trusted_check', return_value={'passed': True, 'officialScore': False}):
                main()
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0][2], calls[1][2])
            for command, _, _ in calls:
                self.assertIn('--restricted', command)
                self.assertIn('--no-session-persistence', command)
                self.assertNotIn('--safe-mode', command)
                self.assertNotIn('--bare', command)
                self.assertNotIn('--resume', command)
            self.assertFalse((workspace / 'task-result.md').exists())
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['kind'], 'local-rehearsal')
            self.assertFalse(summary['officialGradeCreated'])
            self.assertTrue(summary['passed'])


if __name__ == '__main__':
    unittest.main()
