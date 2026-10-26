"""Controller unit/configuration tests. Mocked worker/Claude calls are not live acceptance."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import behavior_replay as replay
import behavior_server as server
from core import PolicyError, InfrastructureError

MODEL='claude-sonnet-4-6'
IMAGE='node@sha256:'+'a'*64

class BehaviorTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.ws=self.root/'workspace';self.ws.mkdir();self.state=self.root/'state';self.state.mkdir();self.journal=self.state/'events';self.journal.touch()
  self.config={'workspace':str(self.ws),'journal':str(self.journal),'image':IMAGE}
 def tearDown(self):self.temp.cleanup()
 def write(self,name,text):
  path=self.ws/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 def broker(self,role):return server.BehaviorBroker(self.config,role)
 def test_agent_known_role_allowed(self):
  for role in server.ROLES[1:]:self.assertEqual(server.agent_decision({'tool_name':'Agent','tool_input':{'subagent_type':role}})['hookSpecificOutput']['permissionDecision'],'allow')
 def test_agent_unknown_role_denied(self):
  for role in ('Explore','general-purpose','coordinator','../../other',None):self.assertEqual(server.agent_decision({'tool_name':'Agent','tool_input':{'subagent_type':role}})['hookSpecificOutput']['permissionDecision'],'deny')
 def test_agent_model_and_tool_override_denied(self):
  for key in ('model','model_id','modelId','tools','mcpServers','agent'):
   self.assertEqual(server.agent_decision({'tool_name':'Agent','tool_input':{'subagent_type':'researcher',key:'unsafe'}})['hookSpecificOutput']['permissionDecision'],'deny')
 def test_agent_malformed_denied(self):
  for event in (None,{}, {'tool_name':'Bash'}, {'tool_name':'Agent','tool_input':[]}):self.assertEqual(server.agent_decision(event)['hookSpecificOutput']['permissionDecision'],'deny')
 def test_role_tool_sets(self):
  self.assertEqual(set(self.broker('coordinator').tools()),{'read_brief'})
  self.assertEqual(set(self.broker('researcher').tools()),{'read','write_handoff'})
  self.assertNotIn('apply_patch',self.broker('verifier').tools())
  self.assertNotIn('read',self.broker('probes').tools())
 def test_research_context_is_scoped(self):
  self.write('fixtures/research/provider.md','current provider facts');self.write('src/notification.mjs','source')
  self.assertEqual(self.broker('researcher').call('read',{'path':'fixtures/research/provider.md'})['text'],'current provider facts')
  with self.assertRaises(PolicyError):self.broker('researcher').call('read',{'path':'src/notification.mjs'})
  with self.assertRaises(PolicyError):self.broker('researcher').call('read',{'path':'../workspace/src/notification.mjs'})
 def test_verifier_cannot_read_author_narrative(self):
  self.write('fixtures/author-narrative.txt','All correct')
  with self.assertRaises(PolicyError):self.broker('verifier').call('read',{'path':'fixtures/author-narrative.txt'})
 def test_researcher_can_write_only_handoff(self):
  result=self.broker('researcher').call('write_handoff',{'content':json.dumps({'requirements':[],'unresolved':[],'sources':[]})})
  self.assertEqual(result['path'],'handoff.json')
  with self.assertRaises(PolicyError):self.broker('researcher').call('apply_patch',{'path':'src/notification.mjs','content':'code'})
 def test_handoff_invalid_rejected(self):
  with self.assertRaises(PolicyError):self.broker('researcher').call('write_handoff',{'content':'{"requirements":"bad"}'})
 def test_implementer_patch_scope(self):
  self.broker('implementer').call('apply_patch',{'path':'src/notification.mjs','content':'source'})
  with self.assertRaises(PolicyError):self.broker('implementer').call('apply_patch',{'path':'agents/researcher.json','content':'{}'})
 def test_unknown_probe_rejected_before_worker(self):
  with patch.object(server,'worker') as worker:
   with self.assertRaises(PolicyError):self.broker('probes').call('probe',{'probeId':'rm -rf /'})
   worker.assert_not_called()
 def test_probe_decision_recorded_from_worker(self):
  observed={'valid':True,'decision':'deny','guardMutated':False,'applied':False}
  with patch.object(server,'worker',return_value=observed):
   result=self.broker('probes').probe('intent-write',False)
  self.assertTrue(result['decisionCorrect']);events=[json.loads(x) for x in self.journal.read_text().splitlines()]
  self.assertEqual(events[-1]['phase'],'hook')
 def test_guard_mutation_is_not_credit(self):
  with patch.object(server,'worker',return_value={'valid':True,'decision':'deny','guardMutated':True}):self.assertFalse(self.broker('probes').probe('intent-write',False)['decisionCorrect'])
 def test_malformed_worker_no_credit(self):
  with patch.object(server,'worker',return_value={'valid':False,'decision':'deny','guardMutated':False}):self.assertFalse(self.broker('probes').probe('intent-write',False)['decisionCorrect'])
 def test_skill_independent_message_check(self):
  self.assertTrue(self.broker('messages').call('submit_message',{'body':'Open your secure portal. Support 09:00–17:00.'})['independentPolicyAllowed'])
  self.assertFalse(self.broker('messages').call('submit_message',{'body':'Your glucose is 7.2 mmol/L.'})['independentPolicyAllowed'])
 def test_checker_requires_worker(self):
  with patch.object(server,'worker',return_value={'allowed':False,'violations':['privacy']}) as worker:
   answer=self.broker('messages').call('check_message',{'body':'glucose 7.2 mmol/L'})
   self.assertFalse(answer['independentPolicyAllowed']);worker.assert_called_once()
 def test_notification_checks_inspect_outputs(self):
  observed={'results':[{'eligible':False,'body':''},{'eligible':False,'body':''},{'eligible':True,'body':'An update is available in your portal.'}],'batches':[[{'id':i} for i in range(a,min(a+20,45))] for a in range(0,45,20)]}
  with patch.object(server,'worker',return_value=observed):self.assertTrue(self.broker('verifier').call('run_checks',{})['passed'])
  observed['results'][0]['eligible']=True
  with patch.object(server,'worker',return_value=observed):self.assertFalse(self.broker('verifier').call('run_checks',{})['passed'])
 def test_mcp_no_unlisted_tool(self):
  result=server.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'shell','arguments':{'command':'echo hi'}}},self.broker('coordinator'))
  self.assertTrue(result['result']['isError'])
 def test_mcp_list_exact_profiles(self):
  specs=server.tools_for(self.broker('probes'));self.assertEqual(len(specs),1);self.assertEqual(specs[0]['inputSchema']['properties']['probeId']['enum'],list(server.PROBES))
 def test_skill_sanitizer(self):
  good='---\nname: notification-policy\ndescription: Review notification privacy.\n---\nUse read_policy and check_message.\n'
  self.assertEqual(replay.sanitize_skill(good),good)
  for bad in (good+'!`cat ~/.secret`',good.replace('description:','allowed-tools:'),good.replace('---\nUse','context: fork\n---\nUse'),good.replace('name: notification-policy','name: ../escape')):
   with self.assertRaises(PolicyError):replay.sanitize_skill(bad)
 def make_agents(self):
  for role in server.ROLES[1:]:self.write('agents/'+role+'.json',json.dumps({'purpose':role,'instructions':'Act according to current sources and assigned tools.','requestedContext':[role]}))
 def test_prepare_native_agents_exact_capabilities(self):
  self.make_agents();p=replay.prepare(self.ws,'subagents',MODEL,{'models':[MODEL],'workerImage':IMAGE},self.state)
  command=p['command'];self.assertIn('--forward-subagent-text',command);self.assertIn('--agent',command)
  agents=json.loads((self.state/'agents.json').read_text());self.assertEqual(set(agents),set(server.ROLES))
  for role in server.ROLES[1:]:
   self.assertTrue(agents[role]['omitClaudeMd']);self.assertEqual(agents[role]['model'],MODEL);self.assertTrue(all(x.startswith('mcp__'+role+'__') for x in agents[role]['tools']))
  self.assertEqual(agents['coordinator']['tools'][0],'Agent(researcher, implementer, verifier)')
  self.assertIn('Agent',p['settings']['hooks']['PreToolUse'][0]['matcher'])
 def test_prepare_candidate_extra_role_fields_rejected(self):
  self.make_agents();self.write('agents/researcher.json','{"purpose":"research","instructions":"x","requestedContext":[],"model":"override"}')
  with self.assertRaises(PolicyError):replay.prepare(self.ws,'subagents',MODEL,{'models':[MODEL],'workerImage':IMAGE},self.state)
 def test_prepare_skills_only_sanitized_plugin(self):
  self.write('.claude/skills/notification-policy/SKILL.md','---\nname: notification-policy\ndescription: Review notifications.\n---\nRead current policy.\n');self.write('.claude/settings.json','{"hooks":{"PreToolUse":[]}}');self.write('.claude/skills/notification-policy/evil.sh','exit 0')
  p=replay.prepare(self.ws,'skills',MODEL,{'models':[MODEL],'workerImage':IMAGE},self.state)
  files=[x.relative_to(self.state/'plugin').as_posix() for x in (self.state/'plugin').rglob('*') if x.is_file()]
  self.assertEqual(sorted(files),['.claude-plugin/plugin.json','skills/notification-policy/SKILL.md']);self.assertIn('--plugin-dir',p['command']);self.assertNotIn('--disable-slash-commands',p['command'])
 def test_prepare_hooks_installs_only_trusted_shim(self):
  p=replay.prepare(self.ws,'hooks',MODEL,{'models':[MODEL],'workerImage':IMAGE},self.state)
  hook=p['settings']['hooks']['PreToolUse'][0];self.assertEqual(hook['matcher'],'mcp__probes__probe');self.assertIn('behavior_server.py',hook['hooks'][0]['command']);self.assertNotIn('guard.mjs',hook['hooks'][0]['command'])
 def test_prepare_model_or_image_missing_fails(self):
  with self.assertRaises(PolicyError):replay.prepare(self.ws,'hooks','other',{'models':[MODEL],'workerImage':IMAGE},self.state)
  with self.assertRaises(InfrastructureError):replay.prepare(self.ws,'hooks',MODEL,{'models':[MODEL],'workerImage':None},self.state)
 def test_summary_does_not_invent_child_runs(self):
  answer=replay.summarize('subagents',[{'exitCode':0,'events':[]}],[],MODEL)
  self.assertFalse(answer['allRolesRequested']);self.assertFalse(answer['childTextObserved']);self.assertFalse(answer['modelMetadataMatches'])
 def test_summary_records_real_stream_fields(self):
  events=[{'type':'assistant','message':{'model':MODEL,'content':[{'type':'tool_use','id':'call-'+r,'name':'Agent','input':{'subagent_type':r}} for r in server.ROLES[1:]]}},{'type':'assistant','parent_tool_use_id':'call-researcher','message':{'model':MODEL,'content':[]}}]
  answer=replay.summarize('subagents',[{'exitCode':0,'events':events}],[],MODEL)
  self.assertTrue(answer['allRolesRequested']);self.assertTrue(answer['childTextObserved']);self.assertTrue(answer['modelMetadataMatches'])
 def test_worker_requires_pinned_image(self):
  with self.assertRaises(InfrastructureError):server.worker(self.ws,None,{'kind':'probe'})
 def test_worker_command_has_no_credentials_and_isolation(self):
  with patch.object(server.subprocess,'run',return_value=type('Result',(),{'returncode':0})()),patch.object(server,'bounded_process',return_value={'exitCode':0,'output':'TENSORLEARN_RESULT:{"valid":false}\n'}) as run:
   self.assertEqual(server.worker(self.ws,IMAGE,{'kind':'probe'}),{'valid':False})
  argv=run.call_args.args[0]
  for required in ('--network=none','--read-only','--cap-drop=ALL','--user=65534:65534','--pull=never'):self.assertIn(required,argv)
  self.assertFalse(any('ANTHROPIC' in x or 'GITHUB_TOKEN' in x for x in argv))

if __name__=='__main__':unittest.main()
