import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route import Controller, InfrastructureError, PolicyError, validate_policy, validate_routing


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = {"models": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"], "workerImage": None}
        self.policy = {"aliases": {"economy": self.config["models"][0], "reasoning": self.config["models"][1]},
                       "maxCalls": 4, "maxInputTokens": 8000, "maxOutputTokens": 2000, "maxRetries": 1,
                       "allowEscalation": True, "clientBudgetTargetUsd": None}
        self.routing = {"stages": {"extract": {"modelAlias": "economy", "maxInputTokens": 4000, "maxOutputTokens": 500, "maxRetries": 0},
                                   "reason": {"modelAlias": "reasoning", "maxInputTokens": 8000, "maxOutputTokens": 1000, "maxRetries": 0}},
                        "routes": [{"taskType": "simple", "stage": "extract"}, {"taskType": "complex", "stage": "reason"}],
                        "escalationPolicy": "Escalate unresolved conflicts to deeper reasoning."}
        self.task = {"id": "one", "taskType": "simple", "prompt": "Identify the owner", "sources": ["policy.md"]}
        self.sources = {"policy.md": "Product and privacy own the channel decision."}

    def response(self, command, prompt, cwd, **kwargs):
        model = command[command.index("--model")+1]
        return json.dumps({"subtype": "success", "total_cost_usd": .01, "modelUsage": {model: {"outputTokens": 20}},
                           "structured_output": {"answer": "Product and privacy.", "needsEscalation": False, "sourcePaths": ["policy.md"]}})

    def controller(self, transport=None):
        return Controller('/fake/claude', self.config, self.policy, self.routing, transport or self.response)

    def test_aliases_use_full_configured_models(self):
        self.policy["aliases"]["economy"] = "sonnet"
        with self.assertRaises(PolicyError):
            self.controller()

    def test_student_cannot_widen_output_limit(self):
        self.routing["stages"]["extract"]["maxOutputTokens"] = 2001
        with self.assertRaises(PolicyError):
            self.controller()

    def test_duplicate_route_rejected(self):
        self.routing["routes"].append(self.routing["routes"][0])
        with self.assertRaises(PolicyError):
            self.controller()

    def test_unknown_stage_rejected(self):
        with self.assertRaises(PolicyError):
            self.controller().run_stage('arbitrary', self.task, self.sources, '')

    def test_input_limit_before_request(self):
        self.routing["stages"]["extract"]["maxInputTokens"] = 100
        controller = self.controller()
        with self.assertRaises(PolicyError):
            controller.run_task(self.task, self.sources, '')
        self.assertEqual(controller.calls, 0)

    def test_request_tools_and_env_are_constrained(self):
        seen = []
        def inspect(command, prompt, cwd, **kwargs):
            seen.append((command, kwargs))
            return self.response(command, prompt, cwd, **kwargs)
        result = self.controller(inspect).run_task(self.task, self.sources, '')
        self.assertFalse(result['escalated'])
        command, kwargs = seen[0]
        self.assertEqual(command[command.index('--tools')+1], '')
        self.assertIn('--strict-mcp-config', command)
        self.assertIn('--safe-mode', command)
        self.assertEqual(kwargs['env_overrides'], {'CLAUDE_CODE_MAX_OUTPUT_TOKENS':'500','CLAUDE_CODE_MAX_RETRIES':'0'})

    def test_max_calls_counts_completed_requests(self):
        self.policy['maxCalls'] = 1
        controller = self.controller()
        controller.run_task(self.task, self.sources, '')
        with self.assertRaises(InfrastructureError):
            controller.run_task(self.task, self.sources, '')
        self.assertEqual(controller.calls, 1)

    def test_no_parallel_admission(self):
        controller = self.controller()
        controller.active = True
        with self.assertRaises(PolicyError):
            controller.run_task(self.task, self.sources, '')

    def test_bounded_retries_count_against_allowance(self):
        self.routing['stages']['extract']['maxRetries'] = 1
        def failed(*args, **kwargs):
            raise InfrastructureError('Unavailable transport')
        controller = self.controller(failed)
        with self.assertRaises(InfrastructureError):
            controller.run_task(self.task, self.sources, '')
        self.assertEqual(controller.calls, 2)
        self.assertFalse(controller.active)

    def test_escalation_once_to_approved_stage(self):
        calls = []
        def escalate(command, prompt, cwd, **kwargs):
            calls.append(command[command.index('--model')+1])
            value = json.loads(self.response(command, prompt, cwd, **kwargs))
            value['structured_output']['needsEscalation'] = True
            return json.dumps(value)
        controller = self.controller(escalate)
        result = controller.run_task(self.task, self.sources, '')
        self.assertTrue(result['escalated'])
        self.assertEqual(calls, self.config['models'])
        self.assertEqual(controller.calls, 2)

    def test_instructor_can_disable_escalation(self):
        self.policy['allowEscalation'] = False
        def escalate(command, prompt, cwd, **kwargs):
            value = json.loads(self.response(command, prompt, cwd, **kwargs))
            value['structured_output']['needsEscalation'] = True
            return json.dumps(value)
        controller = self.controller(escalate)
        self.assertFalse(controller.run_task(self.task, self.sources, '')['escalated'])
        self.assertEqual(controller.calls, 1)

    def test_reported_unapproved_model_stops(self):
        def wrong_model(command, prompt, cwd, **kwargs):
            value = json.loads(self.response(command, prompt, cwd, **kwargs))
            value['modelUsage'] = {'claude-opus-unapproved': {'outputTokens': 20}}
            return json.dumps(value)
        with self.assertRaises(InfrastructureError):
            self.controller(wrong_model).run_task(self.task, self.sources, '')

    def test_reported_output_overrun_stops(self):
        def too_long(command, prompt, cwd, **kwargs):
            value = json.loads(self.response(command, prompt, cwd, **kwargs))
            next(iter(value['modelUsage'].values()))['outputTokens'] = 501
            return json.dumps(value)
        with self.assertRaises(InfrastructureError):
            self.controller(too_long).run_task(self.task, self.sources, '')

    def test_client_budget_target_not_hard_cap(self):
        self.policy['clientBudgetTargetUsd'] = .005
        controller = self.controller()
        controller.run_task(self.task, self.sources, '')
        self.assertEqual(controller.reported_cost, .01)  # CLI-reported overrun is preserved honestly.
        with self.assertRaises(InfrastructureError):
            controller.run_task(self.task, self.sources, '')

    def test_missing_accounting_blocks_retry_when_target_set(self):
        self.policy['clientBudgetTargetUsd'] = 1
        self.routing['stages']['extract']['maxRetries'] = 1
        def unavailable(*args, **kwargs):
            raise InfrastructureError('Transport failed')
        controller = self.controller(unavailable)
        with self.assertRaises(InfrastructureError):
            controller.run_task(self.task, self.sources, '')
        self.assertEqual(controller.calls, 1)

    def test_schema_rejects_unknown_source(self):
        def unrelated(command, prompt, cwd, **kwargs):
            value = json.loads(self.response(command, prompt, cwd, **kwargs))
            value['structured_output']['sourcePaths'] = ['secret.md']
            return json.dumps(value)
        with self.assertRaises(InfrastructureError):
            self.controller(unrelated).run_task(self.task, self.sources, '')


if __name__ == '__main__':
    unittest.main()
