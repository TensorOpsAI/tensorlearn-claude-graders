import ast
import importlib.util
import subprocess
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('report_score', ROOT / 'report_score.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): pass


class ReportTests(unittest.TestCase):
    def test_connection_failure_and_throttles_retry_then_succeed(self):
        failures = [urllib.error.URLError('connection lost'),
                    urllib.error.HTTPError('fixture', 429, 'limited', {}, None),
                    urllib.error.HTTPError('fixture', 503, 'unavailable', {}, None)]
        calls, delays = [], []
        def send(request, timeout):
            calls.append(request)
            self.assertEqual(timeout, 15)
            if failures: raise failures.pop(0)
            return Response()
        report.deliver(b'{"score":0}', 'fixture-token', send, delays.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(delays, [2, 4, 8])
        self.assertTrue(all(r.data == b'{"score":0}' for r in calls))

    def test_rejected_or_redirected_reports_never_retry(self):
        for status in (301, 400, 401, 403, 409):
            calls = []
            def send(request, timeout):
                calls.append(request)
                raise urllib.error.HTTPError('fixture', status, 'rejected', {}, None)
            with self.assertRaises(RuntimeError): report.deliver(b'{}', 'fixture', send, lambda _: self.fail('unexpected retry'))
            self.assertEqual(len(calls), 1)

    def test_exhausted_transport_failures_are_bounded(self):
        calls, delays = [], []
        def send(request, timeout):
            calls.append(request)
            raise TimeoutError()
        with self.assertRaises(RuntimeError): report.deliver(b'{}', 'fixture', send, delays.append)
        self.assertEqual(len(calls), 5)
        self.assertEqual(delays, [2, 4, 8, 16])

    def test_workflow_generator_preserves_callback_permissions_and_artifact_first(self):
        path = ROOT / '.github/workflows/grade.yml'
        before = path.read_bytes()
        subprocess.run(['python3', str(ROOT / 'build_workflow.py')], check=True)
        self.assertEqual(before, path.read_bytes())
        workflow = path.read_text()
        self.assertIn('  id-token: write', workflow)
        self.assertLess(workflow.index('name: Upload score'), workflow.index('name: Report score to TensorLearn'))
        self.assertIn('continue-on-error: true', workflow)
        self.assertIn('class NoRedirect', workflow)

