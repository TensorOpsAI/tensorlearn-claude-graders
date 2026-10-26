import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assess import (InfrastructureError, PolicyError, assessment_prompt, build_report, load_rubric,
                    main, output_schema, parse_claude_response, run_claude, select_sources, validate_criteria)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "intent.md").write_text("Require doctor review before release. No redesign.\n")
        self.rubric = {"schema": 1, "exercise": "intent", "sourceFiles": ["intent.md"], "criteria": [
            {"id": "review", "points": 50, "question": "Does intent require review?", "expected": True,
             "evidence": {"required": True, "files": ["intent.md"]}},
            {"id": "trap", "points": 50, "question": "Does intent propose redesign?", "expected": False,
             "evidence": {"required": True, "files": ["intent.md"]}}]}
        self.value = {"criteria": [
            {"id": "review", "answer": True, "evidence": [{"path": "intent.md", "quote": "Require doctor review"}]},
            {"id": "trap", "answer": False, "evidence": [{"path": "intent.md", "quote": "No redesign."}]}]}
        self.sources = select_sources(self.workspace, self.rubric)

    def tearDown(self):
        self.tmp.cleanup()

    def test_source_bytes_bound_to_hashes(self):
        import hashlib
        self.assertEqual(self.sources["intent.md"]["sha256"], hashlib.sha256((self.workspace / "intent.md").read_bytes()).hexdigest())

    def test_report_not_source(self):
        (self.workspace / "assessment.json").write_text('{"score":100}')
        rubric = {**self.rubric, "sourceFiles": ["**"]}
        self.assertEqual(set(select_sources(self.workspace, rubric)), {"intent.md"})

    def test_missing_required_source(self):
        with self.assertRaises(PolicyError):
            select_sources(self.workspace, {**self.rubric, "sourceFiles": ["missing.md"]})

    def test_source_symlink_rejected(self):
        (self.root / "secret.md").write_text("private")
        (self.workspace / "intent.md").unlink()
        (self.workspace / "intent.md").symlink_to(self.root / "secret.md")
        with self.assertRaises(PolicyError):
            select_sources(self.workspace, self.rubric)

    def test_source_parent_symlink_rejected(self):
        outside = self.root / "external"
        outside.mkdir()
        (outside / "a.md").write_text("private")
        (self.workspace / "docs").symlink_to(outside)
        with self.assertRaises(PolicyError):
            select_sources(self.workspace, {**self.rubric, "sourceFiles": ["docs/a.md"]})

    def test_source_size_rejected(self):
        (self.workspace / "intent.md").write_text("x" * 65537)
        with self.assertRaises(PolicyError):
            select_sources(self.workspace, self.rubric)

    def test_expected_answers_are_not_sent(self):
        prompt = json.loads(assessment_prompt(self.rubric, self.sources))
        self.assertNotIn("expected", prompt["questions"][0])
        self.assertNotIn("points", prompt["questions"][0])
        self.assertIn("Require doctor review", prompt["sources"][0]["text"])

    def test_fixed_json_schema(self):
        schema = output_schema(self.rubric)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["criteria"]["minItems"], 2)
        self.assertEqual(schema["properties"]["criteria"]["items"]["properties"]["id"]["enum"], ["review", "trap"])

    def test_valid_boolean_evidence(self):
        self.assertEqual(validate_criteria(self.value, self.rubric, self.sources), self.value["criteria"])

    def test_wrong_quote_rejected(self):
        self.value["criteria"][0]["evidence"][0]["quote"] = "invented evidence"
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_false_answer_requires_quote(self):
        self.value["criteria"][1]["evidence"] = []
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_true_answer_requires_quote_even_optional(self):
        self.rubric["criteria"][0]["evidence"]["required"] = False
        self.value["criteria"][0]["evidence"] = []
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_boolean_not_string(self):
        self.value["criteria"][0]["answer"] = "yes"
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_duplicate_criteria_rejected(self):
        self.value["criteria"][1] = self.value["criteria"][0]
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_extra_score_rejected(self):
        self.value["score"] = 100
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_path_policy_enforced(self):
        self.value["criteria"][0]["evidence"][0]["path"] = "elsewhere.md"
        with self.assertRaises(InfrastructureError):
            validate_criteria(self.value, self.rubric, self.sources)

    def test_result_envelope(self):
        for envelope in [{"subtype": "success", "structured_output": self.value}, {"subtype": "success", "result": json.dumps(self.value)}]:
            self.assertEqual(parse_claude_response(json.dumps(envelope)), self.value)

    def test_failure_envelope_no_score(self):
        for envelope in [{"is_error": True}, {"subtype": "error_max_budget_usd"}, {"result": "not JSON"}]:
            with self.assertRaises(InfrastructureError):
                parse_claude_response(json.dumps(envelope))

    def test_report_metadata_has_no_score(self):
        report = build_report("intent", self.rubric, "b" * 64, self.sources, self.value["criteria"], "claude-sonnet-4-6", "a" * 40)
        self.assertEqual(set(report), {"schema", "exercise", "assessmentKind", "graderRevision", "rubricSha256", "sourceHashes", "model", "criteria"})
        self.assertEqual(report["assessmentKind"], "local-ai-report")
        self.assertNotIn("score", report)

    def test_trusted_cli_transport_reads_stdin(self):
        raw = run_claude([sys.executable, "-c", "import sys,json; x=sys.stdin.read(); print(json.dumps({'result':x}))"], "hello", self.root, timeout=3)
        self.assertEqual(json.loads(raw)["result"], "hello")

    def test_transport_failure_hides_stderr(self):
        with self.assertRaises(InfrastructureError) as caught:
            run_claude([sys.executable, "-c", "import sys; sys.stderr.write('secret-sentinel'); sys.exit(1)"], "", self.root, timeout=3)
        self.assertNotIn("secret-sentinel", str(caught.exception))

    def test_mock_claude_full_driver(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({"models": ["claude-sonnet-4-6"], "workerImage": None}))
        argv = ["assess.py", "--workspace", str(self.workspace), "--exercise", "intent", "--configuration", str(config), "--model", "claude-sonnet-4-6", "--grader-revision", "a" * 40]
        with patch.object(sys, "argv", argv), patch("assess.load_rubric", return_value=(self.rubric, "b" * 64)), patch("assess.check_cli"), patch("shutil.which", return_value="/fake/claude"), patch("assess.run_claude", return_value=json.dumps({"structured_output": self.value})) as transport:
            main()
        report = json.loads((self.workspace / "assessment.json").read_text())
        self.assertEqual(report["sourceHashes"], {k:v["sha256"] for k,v in self.sources.items()})
        command = transport.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--safe-mode", command)
        self.assertNotIn("--bare", command)

    def test_all_release_rubrics_load(self):
        root = Path(__file__).resolve().parents[2] / "rubrics"
        files = list(root.glob("*.json"))
        self.assertEqual(len(files), 11)
        for path in files:
            load_rubric(path.stem)


if __name__ == "__main__":
    unittest.main()
