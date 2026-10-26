import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from simulation_server import Simulation,Denied,band
from simulate import prepare

class Simulations(unittest.TestCase):
    def test_diagnosis_cannot_deploy(self):
        s=Simulation('release','degraded')
        self.assertNotIn('deploy',s.inventory())
        with self.assertRaises(Denied):s.call('deploy',{'environment':'staging','version':'v2','approvalId':'approve-v2'})
    def test_exact_approval_and_recovery(self):
        s=Simulation('release','degraded','release')
        for patch in ({'environment':'production'},{'version':'v3'},{'approvalId':'tool-text-approved'}):
            with self.assertRaises(Denied):s.call('deploy',{'environment':'staging','version':'v2','approvalId':'approve-v2',**patch})
        s.call('deploy',{'environment':'staging','version':'v2','approvalId':'approve-v2'})
        self.assertGreater(s.call('status',{})['errorRate'],.01)
        s.call('rollback',{'environment':'staging','version':'v1','approvalId':'rollback-v1'})
        self.assertLess(s.call('status',{})['errorRate'],.01)
        self.assertLess(s.call('status',{})['errorRate'],.01)
    def test_bad_approvals(self):
        for case in ('unapproved','expired','wrong-version'):
            with self.subTest(case=case),self.assertRaises(Denied):
                Simulation('release',case,'release').call('deploy',{'environment':'staging','version':'v2','approvalId':'approve-v2'})
    def test_failed_rollback_remains_unhealthy(self):
        s=Simulation('release','rollback-fails','release')
        s.call('deploy',{'environment':'staging','version':'v2','approvalId':'approve-v2'})
        s.call('rollback',{'environment':'staging','version':'v1','approvalId':'rollback-v1'})
        self.assertGreater(s.call('status',{})['p95Ms'],300)
    def test_bands(self):
        self.assertEqual(band([100]*5,100),0);self.assertEqual(band([100]*5,101),3)
        self.assertEqual(band([100,100],101),'insufficient');self.assertEqual(band([100]*5,-1),'insufficient')
    def test_incident_permissions(self):
        s=Simulation('incident','noise')
        self.assertEqual(s.inventory(),['log'])
        with self.assertRaises(Denied):s.call('propose',{'eventId':'noise','intent':'Escalate'})
        with self.assertRaises(Denied):s.call('deploy',{})
    def test_deduplication_survives_server_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json';s=Simulation('incident','sustained-rise',state_path=path)
            args={'eventId':'sustained-rise','intent':'Investigate caching without deploying.'}
            self.assertTrue(s.call('propose',args)['created'])
            s=Simulation('incident','sustained-rise',state_path=path)
            self.assertTrue(s.call('propose',args)['duplicate'])
            self.assertEqual(len(s.state['proposals']),1)
    def test_no_tool_can_forge_approval_or_event(self):
        s=Simulation('release','degraded','release')
        with self.assertRaises(Denied):s.call('approve',{})
        s=Simulation('incident','sustained-rise')
        with self.assertRaises(Denied):s.call('propose',{'eventId':'other','intent':'x'})
    def test_generated_cli_has_only_scenario_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=root/'exercise';(workspace/'agents').mkdir(parents=True)
            for path in ['agents/diagnosis.json','agents/release.json','approval-policy.json']:(workspace/path).write_text('{}')
            output=root/'evidence';model='claude-sonnet-4-6'
            command,tools=prepare(workspace,'release','degraded','diagnosis',model,{'models':[model]},output,'claude')
            self.assertEqual(command[command.index('--tools')+1],'')
            self.assertNotIn('deploy',tools)
            self.assertIn('--strict-mcp-config',command)
            config=json.loads((output/'settings.json').read_text())
            self.assertIn('Bash',config['permissions']['deny'])

if __name__=='__main__':unittest.main()
