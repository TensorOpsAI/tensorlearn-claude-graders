"""Host comparison tests plus opt-in real Docker execution, without AI calls."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import artifact_checks as checks
from bug_assessment import CandidateFailure, Unavailable
IMAGE='node:22.18.0-alpine@sha256:1b2479dd35a99687d6638f5976fd235e26c5b37e8122f786fcd5fe231d63de5b'
GOOD_SKILL=r'''
export function checkMessage(input){
 const body=input?.body;
 const violations=[];
 if(typeof body!=='string'||!body.trim())violations.push('invalid-body');
 else if(/glucose|hemoglobin|positive result|diabetes|metformin|chemotherapy|\d+(?:\.\d+)?\s*(?:mmol\/L|mg\/dL)/i.test(body))violations.push('clinical-content');
 return {allowed:violations.length===0,violations};
}
'''
GOOD_EVAL=r'''
export function evaluate(o){
 const violations=[];
 if(!o||!Array.isArray(o.requests)||!o.requests.length||!Array.isArray(o.upstreamCalls)||!Array.isArray(o.responses)||!Array.isArray(o.logs)||!o.requirements||!Array.isArray(o.requirements.forbiddenLogFragments))return {pass:false,violations:['invalid-observation']};
 const limit=o.requirements.maxRequestsPerSecond,ttl=o.requirements.cacheTtlMs;
 if(!Number.isFinite(limit)||limit<=0||!Number.isFinite(ttl)||ttl<=0)return {pass:false,violations:['invalid-policy']};
 const calls=o.upstreamCalls.slice().sort((a,b)=>a.atMs-b.atMs),last=new Map();
 for(let i=0;i<calls.length;i++){
  const x=calls[i];
  if(!x||typeof x.customerCode!=='string'||!Number.isFinite(x.atMs)){violations.push('invalid-call');continue;}
  if(last.has(x.customerCode)&&x.atMs-last.get(x.customerCode)<ttl)violations.push('cache');
  last.set(x.customerCode,x.atMs);
  if(i>=limit&&x.atMs-calls[i-limit].atMs<1000)violations.push('rate');
 }
 if(o.responses.length!==o.requests.length)violations.push('responses');
 if(o.logs.some(log=>typeof log!=='string'||o.requirements.forbiddenLogFragments.some(word=>log.toLowerCase().includes(word.toLowerCase()))))violations.push('privacy');
 return {pass:violations.length===0,violations};
}
'''
GOOD_DETECT=r'''
export function detect(history,current,policy){
 const invalid={band:'insufficient',mean:null,stddev:null,z:null};
 if(!Array.isArray(history)||history.length<policy.minSamples||[...history,current].some(x=>typeof x!=='number'||!Number.isFinite(x)||x<0))return invalid;
 const mean=history.reduce((a,b)=>a+b,0)/history.length;
 const stddev=Math.sqrt(history.reduce((sum,x)=>sum+(x-mean)**2,0)/history.length);
 if(stddev===0)return {band:current>mean?3:0,mean,stddev:0,z:current>mean?null:0};
 const z=(current-mean)/stddev;
 return {band:policy.thresholds.filter(x=>z>=x).length,mean,stddev,z};
}
'''
GOOD_INCIDENT_EVAL=r'''
export function evaluate(o){
 const violations=[];
 if(!o||typeof o.upstreamCallsPerSecond!=='number'||!Number.isFinite(o.upstreamCallsPerSecond)||o.upstreamCallsPerSecond<0||o.upstreamCallsPerSecond>50)violations.push('rate');
 if(o?.cacheEnabled!==true)violations.push('cache');
 if(o?.duplicateProposals!==0)violations.push('duplicates');
 if(o?.clinicalPayloadLogged!==false)violations.push('privacy');
 return {pass:violations.length===0,violations};
}
'''

class HostTests(unittest.TestCase):
 def test_matrix_ids_unique(self):
  for key in checks.ENTRIES:
   matrix=checks.cases(key);self.assertEqual(len(matrix),len({x['id'] for x in matrix}));self.assertTrue(checks.critical_case_ids(key)<={x['id'] for x in matrix})
 def test_expected_detector_exact_boundaries(self):
  for band,current in ((0,1.99),(1,2),(2,3),(3,4)):
   self.assertEqual(checks.expected_detector([0,0,2,2],current,{'minSamples':4,'thresholds':[1,2,3]})['band'],band)
 def test_expected_detector_invalid_bool(self):self.assertEqual(checks.expected_detector([100]*5,True,{'minSamples':5,'thresholds':[1,2,3]})['band'],'insufficient')
 def test_semantic_boolean_and_violations_contract(self):
  item=checks.skill_cases()[0];good={'value':{'allowed':True,'violations':[]},'after':copy.deepcopy(item['args'])}
  self.assertTrue(checks.compare(item,good))
  for value in ({'allowed':1,'violations':[]},{'allowed':True,'violations':['bad']},{'allowed':True,'violations':[],'score':100}):self.assertFalse(checks.compare(item,{'value':value,'after':item['args']}))
 def test_candidate_input_mutation_rejected(self):
  item=checks.skill_cases()[0];self.assertFalse(checks.compare(item,{'value':{'allowed':True,'violations':[]},'after':[{'body':'changed'}]}))
 def test_detector_numeric_tolerance_and_types(self):
  item=checks.incident_cases()[0];self.assertTrue(checks.compare(item,{'value':item['expected'],'after':item['args']}))
  value={**item['expected'],'band':False};self.assertFalse(checks.compare(item,{'value':value,'after':item['args']}))
 def test_sources_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'checker').mkdir();(root/'other.mjs').write_text('export function checkMessage(){}');(root/'checker/check-message.mjs').symlink_to(root/'other.mjs')
   with self.assertRaises(CandidateFailure):checks.sources(root,'skills')
 def test_sources_relative_helpers_collected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'evals').mkdir();(root/'evals/assert.mjs').write_text('export {evaluate} from "./helper.mjs";');(root/'evals/helper.mjs').write_text(GOOD_EVAL)
   self.assertEqual(set(checks.sources(root,'evals')),{'evals/assert.mjs','evals/helper.mjs'})
 def test_missing_module_is_candidate_failure(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'checker').mkdir()
   with self.assertRaises(CandidateFailure):checks.sources(d,'skills')
 def test_invalid_image_unavailable(self):
  with self.assertRaises(Unavailable):checks.assess('/tmp','skills','node:latest')
 def test_worker_inputs_do_not_contain_expectations(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'checker').mkdir();(root/'checker/check-message.mjs').write_text(GOOD_SKILL)
   def fake_worker(candidate,trusted,image,argv):
    data=json.loads((trusted/'inputs.json').read_text());self.assertTrue(all('expected' not in x and 'id' not in x for x in data));return 1,b'',b''
   with patch.object(checks.subprocess,'run',return_value=type('R',(),{'returncode':0})()),patch.object(checks,'worker',side_effect=fake_worker):
    result=checks.assess(root,'skills',IMAGE);self.assertTrue(all(not x['passed'] for x in result))

@unittest.skipUnless(os.environ.get('RUN_DOCKER')=='1','Set RUN_DOCKER=1 for real isolated execution (no AI)')
class DockerTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def write(self,name,text):p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
 def assess(self,key):return checks.assess(self.root,key,IMAGE)
 def assertAllPass(self,key):
  result=self.assess(key);self.assertTrue(all(x['passed'] for x in result),[x for x in result if not x['passed']]);return result
 def test_skills_good(self):self.write('checker/check-message.mjs',GOOD_SKILL);self.assertAllPass('skills')
 def test_skills_allow_all_and_deny_all(self):
  for allowed in (True,False):
   self.write('checker/check-message.mjs','export function checkMessage(){return '+json.dumps({'allowed':allowed,'violations':[] if allowed else ['blocked']})+';}')
   result=self.assess('skills');self.assertTrue(any(not x['passed'] for x in result));self.assertTrue(any(x['passed'] for x in result));self.assertTrue(any(not x['passed'] and x['id'] in checks.critical_case_ids('skills') for x in result))
 def test_evals_good(self):self.write('evals/assert.mjs',GOOD_EVAL);self.assertAllPass('evals')
 def test_evals_relative_helper(self):self.write('evals/assert.mjs','export {evaluate} from "./helper.mjs";');self.write('evals/helper.mjs',GOOD_EVAL);self.assertAllPass('evals')
 def test_evals_alwayspass_and_alwaysfail(self):
  for passed in (True,False):
   self.write('evals/assert.mjs','export function evaluate(){return '+json.dumps({'pass':passed,'violations':[] if passed else ['bad']})+';}')
   result=self.assess('evals');self.assertTrue(any(not x['passed'] for x in result));self.assertTrue(any(x['passed'] for x in result))
 def test_incident_good(self):self.write('detector/detect.mjs',GOOD_DETECT);self.write('evals/assert.mjs',GOOD_INCIDENT_EVAL);self.assertAllPass('incident')
 def test_incident_constant_detector_and_pass_eval(self):
  self.write('detector/detect.mjs',"export function detect(){return {band:0,mean:0,stddev:0,z:0};}");self.write('evals/assert.mjs',"export function evaluate(){return {pass:true,violations:[]};}")
  result=self.assess('incident');self.assertTrue(any(not x['passed'] for x in result));self.assertTrue(any(not x['passed'] and x['id'] in checks.critical_case_ids('incident') for x in result))
 def test_host_import_not_available(self):
  self.write('checker/check-message.mjs',"import fs from 'node:fs'; export function checkMessage(){return {allowed:true,violations:[]};}")
  self.assertTrue(all(not x['passed'] for x in self.assess('skills')))
 def test_mutation_rejected_in_real_worker(self):
  self.write('checker/check-message.mjs',GOOD_SKILL.replace('const body=input?.body;','const body=input?.body; if(input)input.extra=1;'))
  self.assertTrue(all(not x['passed'] for x in self.assess('skills')))

if __name__=='__main__':unittest.main()
