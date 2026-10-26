import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import report_assessment as assessment

class ReportTests(unittest.TestCase):
    def setup_fixture(self,root):
        text='Doctor review is required. No clinical details. Portal redesign is excluded.'
        (root/'intent.md').write_text(text)
        rubric={'schema':1,'exercise':'intent','sourceFiles':['intent.md'],'criteria':[
            {'id':'review','points':60,'question':'Requires review?','expected':True,'evidence':{'required':True,'files':['intent.md']}},
            {'id':'leak','points':40,'question':'Allows clinical details?','expected':False,'criticalCap':25,'evidence':{'required':True,'files':['intent.md']}}]}
        raw=json.dumps(rubric).encode()
        report={'schema':1,'exercise':'intent','assessmentKind':'local-ai-report','graderRevision':'a'*40,
            'rubricSha256':hashlib.sha256(raw).hexdigest(),'sourceHashes':{'intent.md':hashlib.sha256(text.encode()).hexdigest()},
            'model':'claude-sonnet-4-6','criteria':[
                {'id':'review','answer':True,'evidence':[{'path':'intent.md','quote':'Doctor review is required.'}]},
                {'id':'leak','answer':False,'evidence':[{'path':'intent.md','quote':'No clinical details.'}]}]}
        return raw,report
    def run_report(self,root,raw,report):
        (root/'assessment.json').write_text(json.dumps(report))
        return assessment.assess(root,'intent','a'*40,raw)
    def test_good_and_critical_trap(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw,report=self.setup_fixture(root)
            self.assertEqual(self.run_report(root,raw,report)[1],100)
            report['criteria'][1]['answer']=True
            self.assertEqual(self.run_report(root,raw,report)[1],25)
    def test_stale_and_fabricated_evidence(self):
        for mutation in ('hash','revision','rubric','quote','path','type','duplicate','missing','extra','model'):
            with tempfile.TemporaryDirectory() as temp,self.subTest(mutation=mutation):
                root=Path(temp);raw,report=self.setup_fixture(root)
                if mutation=='hash': (root/'intent.md').write_text('changed')
                if mutation=='revision': report['graderRevision']='b'*40
                if mutation=='rubric': report['rubricSha256']='b'*64
                if mutation=='quote': report['criteria'][0]['evidence'][0]['quote']='invented'
                if mutation=='path': report['criteria'][0]['evidence'][0]['path']='../../secret'
                if mutation=='type': report['criteria'][0]['answer']='true'
                if mutation=='duplicate': report['criteria'][1]=report['criteria'][0]
                if mutation=='missing': report['criteria'][0]['evidence']=[]
                if mutation=='extra': report['score']=100
                if mutation=='model': report['model']='sonnet'
                with self.assertRaises(assessment.Invalid):self.run_report(root,raw,report)
    def test_report_does_not_hash_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw,report=self.setup_fixture(root)
            self.run_report(root,raw,report)
            self.assertEqual(set(assessment.sources(root,json.loads(raw))),{'intent.md'})
    def test_missing_source_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw,report=self.setup_fixture(root)
            with self.assertRaises(assessment.Invalid):assessment.assess(root,'intent','a'*40,raw)
            (root/'intent.md').unlink()
            with self.assertRaises(assessment.Invalid):self.run_report(root,raw,report)

if __name__=='__main__':unittest.main()
