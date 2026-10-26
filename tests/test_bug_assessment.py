import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bug_assessment as bug

IMAGE='node:22.18.0-alpine@sha256:1b2479dd35a99687d6638f5976fd235e26c5b37e8122f786fcd5fe231d63de5b'
GOOD='''export function getClaim(claims,viewerId,claimId) {
 const id=x=>typeof x==='string'&&x.trim()?x.trim():null;
 const viewer=id(viewerId), target=id(claimId);
 if(!Array.isArray(claims)||!viewer||!target)return null;
 return claims.find(x=>x&&id(x.id)===target&&id(x.ownerId)===viewer)??null;
}'''
TEST='''import test from 'node:test';
import assert from 'node:assert/strict';
import {getClaim} from '../../src/claims.mjs';
test('reject a prefix owner',()=>assert.equal(getClaim([{id:'c',ownerId:'user-42'}],'user-4','c'),null));
'''

def fixture(root,source=GOOD,test=TEST):
    (root/'src').mkdir()
    (root/'tests/student').mkdir(parents=True)
    (root/'src/claims.mjs').write_text(source)
    if test is not None:
        (root/'tests/student/regression.test.mjs').write_text(test)
    return root

class UnitTests(unittest.TestCase):
    def test_strict_result_parser(self):
        self.assertEqual(bug.decode_results(b'[{"value":null,"after":[]}]'),[{'value':None,'after':[]}])
        for data in [b'[]\n[]',b'{"a":1,"a":2}',b'{"a":NaN}',b'not-json',b'\xff']:
            with self.subTest(data=data),self.assertRaises(bug.CandidateFailure):bug.decode_results(data)
    def test_expected_answers_not_sent_to_worker(self):
        matrix=bug.cases()
        sent=[x['input'] for x in matrix]
        self.assertTrue(all(set(x)=={'claims','viewer','claim'} for x in sent))
        self.assertNotIn('expected',bug.HARNESS)
        self.assertGreaterEqual(len(matrix),15)
    def test_snapshot_baseline_and_student_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir();fixture(source)
            tests=bug.snapshot(source,root/'candidate')
            bug.snapshot(source,root/'original',baseline=True)
            self.assertEqual(tests,['tests/student/regression.test.mjs'])
            self.assertEqual((root/'original/src/claims.mjs').read_text(),bug.BASELINE)
            self.assertEqual((root/'candidate/src/claims.mjs').read_text(),GOOD)
            self.assertEqual((root/'original/tests/student/regression.test.mjs').read_text(),TEST)
    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir();fixture(source)
            (source/'tests/student/escape').symlink_to('/etc/passwd')
            with self.assertRaises(bug.CandidateFailure):bug.snapshot(source,root/'candidate')
    def test_unpinned_image_unavailable(self):
        with self.assertRaises(bug.Unavailable):bug.assess(Path('.'),'node:22')

class DockerTests(unittest.TestCase):
    def evaluate(self,source=GOOD,test=TEST):
        with tempfile.TemporaryDirectory() as folder:
            return bug.assess(fixture(Path(folder),source,test),IMAGE)
    def test_fixed_function_and_real_regression_pass(self):
        checks=self.evaluate()
        self.assertTrue(all(x['passed'] for x in checks),checks)
    def test_buggy_function_fails_official_and_student_checks(self):
        checks=self.evaluate(bug.BASELINE)
        found={x['id']:x['passed'] for x in checks}
        self.assertFalse(found['prefix-owner'])
        self.assertFalse(found['student-regression-differential'])
    def test_placeholder_and_always_pass_cannot_establish_regression(self):
        for test in ["throw new Error('placeholder');", "import test from 'node:test';test('empty',()=>{});",None]:
            with self.subTest(test=test):
                checks=self.evaluate(test=test)
                self.assertFalse(checks[-1]['passed'])
                self.assertTrue(all(x['passed'] for x in checks[:-1]))
    def test_candidate_cannot_replace_json_with_process_stdout(self):
        checks=self.evaluate("process.stdout.write('[]'); export function getClaim(){return null;}")
        self.assertFalse(any(x['passed'] for x in checks[:-1]))
    def test_source_import_and_input_mutation_fail(self):
        checks=self.evaluate("import fs from 'node:fs'; export function getClaim(){return null;}")
        self.assertFalse(any(x['passed'] for x in checks[:-1]))
        checks=self.evaluate("export function getClaim(claims){if(Array.isArray(claims))claims.length=0;return null;}")
        self.assertFalse(next(x['passed'] for x in checks if x['id']=='prefix-owner'))

if __name__=='__main__':
    integration='--integration' in sys.argv
    if integration:sys.argv.remove('--integration')
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(UnitTests)
    if integration:suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DockerTests))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
