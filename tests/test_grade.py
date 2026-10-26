import base64
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import grade

class GraderTests(unittest.TestCase):
    def test_numeric_answers(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for value,expected in [(2,True),(2.0,True),(3,False),(-2,False),(0,False)]:
                (root/'answer.json').write_text(json.dumps({'answer':value}))
                self.assertEqual(grade.arithmetic(root)[0]['passed'],expected)
    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for data in [b'{"answer":null}',b'{"answer":true}',b'{"answer":"2"}',b'{"answer":2,"extra":0}',b'{"answer":2,"answer":3}',b'{"answer":NaN}',b'{"answer":Infinity}',b'{"answer":1e999}',b'\xff',b'[]',b' '*1025]:
                (root/'answer.json').write_bytes(data)
                with self.subTest(data=data[:50]),self.assertRaises(grade.Invalid): grade.arithmetic(root)
    def test_path_policy(self):
        self.assertTrue(grade.editable('src/a.mjs',['src/**']))
        self.assertFalse(grade.editable('src2/a.mjs',['src/**']))
        self.assertFalse(grade.editable('intent.md',['docs/**']))
    def test_scoring(self):
        score=grade.score('preflight','a'*40,'b'*40,[{'id':'a','passed':True}])
        self.assertEqual(score['score'],10)
        self.assertEqual(sum(score['points'].values()),score['score'])
        self.assertEqual(grade.score('preflight','a'*40,'b'*40,[],True)['status'],'invalid')
    def test_no_invented_behavioral_score(self):
        with self.assertRaises(grade.Unavailable): grade.isolated_checks(Path('.'),'intent','unused')
    def test_bug_critical_and_missing_regression_caps(self):
        keys=['exact-owner','trim-identifiers','duplicate-id-other-owner','prefix-owner','empty-owner','whitespace-owner','student-regression-differential']
        checks=[{'id':k,'passed':True} for k in keys]
        self.assertEqual(grade.score('bug-fix','a'*40,'b'*40,checks)['score'],100)
        checks[-1]['passed']=False
        self.assertEqual(grade.score('bug-fix','a'*40,'b'*40,checks)['score'],70)
        checks[0]['passed']=False
        self.assertEqual(grade.score('bug-fix','a'*40,'b'*40,checks)['score'],25)
    def evidence(self):
        repo='learner/labs';head='a'*40;base='b'*40;tree='c'*40;grader='d'*40;base_tree='f'*40
        files={'README.md':b'protected','exercises/preflight/answer.json':b'{"answer":2}',
               'exercises/preflight/exercise.json':b'{}','.github/workflows/preflight.yml':grade.caller('preflight',grader).encode()}
        manifest={'exercises':{'preflight':{'writeAllow':['answer.json']}},'files':{p:grade.blob_sha(v) for p,v in files.items() if not p.startswith('.github/')}}
        manifest['files']['exercises/preflight/answer.json']=grade.blob_sha(b'{"answer":null}')
        pull={'number':1,'state':'open','merged_at':None,'head':{'sha':head,'repo':{'full_name':repo}},'base':{'sha':base,'ref':'main','repo':{'full_name':repo}}}
        data={f'repos/{repo}/pulls/1':pull,f'repos/{repo}/git/commits/{head}':{'sha':head,'tree':{'sha':tree}},
              f'repos/{repo}/git/trees/{tree}?recursive=1':{'sha':tree,'truncated':False,'tree':[{'path':p,'sha':grade.blob_sha(v),'size':len(v),'mode':'100644','type':'blob'} for p,v in files.items()]}}
        baseline=dict(manifest['files'])
        baseline['.github/workflows/preflight.yml']=grade.blob_sha(grade.caller('preflight',grader).encode())
        data[f'repos/{repo}/git/commits/{base}']={'sha':base,'tree':{'sha':base_tree}}
        data[f'repos/{repo}/git/trees/{base_tree}?recursive=1']={'sha':base_tree,'truncated':False,
            'tree':[{'path':p,'sha':sha,'mode':'100644','type':'blob'} for p,sha in baseline.items()]}
        for v in files.values():
            sha=grade.blob_sha(v);data[f'repos/{repo}/git/blobs/{sha}']={'sha':sha,'encoding':'base64','size':len(v),'content':base64.b64encode(v).decode()}
        return repo,head,grader,manifest,data
    def test_snapshot(self):
        repo,head,sha,manifest,data=self.evidence()
        with tempfile.TemporaryDirectory() as d:
            grade.snapshot(data.__getitem__,repo,head,1,'preflight',sha,manifest,Path(d))
            self.assertEqual(grade.arithmetic(Path(d))[0]['passed'],True)
    def test_base_tamper_cannot_be_hidden_by_restored_head(self):
        repo,head,sha,manifest,data=self.evidence()
        base_tree=data[f'repos/{repo}/git/trees/{"f"*40}?recursive=1']
        base_tree['tree'][0]['sha']='e'*40
        # Head still contains every expected protected file and a correct answer;
        # nevertheless its PR diff would restore a protected base file.
        with tempfile.TemporaryDirectory() as d,self.assertRaisesRegex(grade.Invalid,'PR base differs'):
            grade.snapshot(data.__getitem__,repo,head,1,'preflight',sha,manifest,Path(d))
    def test_base_tree_rejects_incomplete_or_changed_baseline(self):
        for mutation in ['missing','extra','symlink','truncated','commit-id','tree-id','duplicate','answer']:
            repo,head,sha,manifest,data=self.evidence()
            tree=data[f'repos/{repo}/git/trees/{"f"*40}?recursive=1']
            if mutation=='missing': tree['tree'].pop(0)
            if mutation=='extra': tree['tree'].append({'path':'extra','sha':'e'*40,'mode':'100644','type':'blob'})
            if mutation=='symlink': tree['tree'][0]['mode']='120000'
            if mutation=='truncated': tree['truncated']=True
            if mutation=='commit-id': data[f'repos/{repo}/git/commits/{"b"*40}']['sha']='e'*40
            if mutation=='tree-id': tree['sha']='e'*40
            if mutation=='duplicate': tree['tree'].append(dict(tree['tree'][0]))
            if mutation=='answer': tree['tree'][1]['sha']=grade.blob_sha(b'{"answer":2}')
            with tempfile.TemporaryDirectory() as d,self.subTest(mutation=mutation),self.assertRaises((grade.Invalid,grade.Unavailable)):
                grade.snapshot(data.__getitem__,repo,head,1,'preflight',sha,manifest,Path(d))
    def test_baseline_has_no_circular_starter_commit_dependency(self):
        repo,head,sha,manifest,data=self.evidence()
        self.assertNotIn('starterSha',manifest)
        replacement='1'*40
        old=data.pop(f'repos/{repo}/git/commits/{"b"*40}')
        old['sha']=replacement
        data[f'repos/{repo}/git/commits/{replacement}']=old
        data[f'repos/{repo}/pulls/1']['base']['sha']=replacement
        with tempfile.TemporaryDirectory() as d:
            grade.snapshot(data.__getitem__,repo,head,1,'preflight',sha,manifest,Path(d))
            self.assertTrue(grade.arithmetic(Path(d))[0]['passed'])
    def test_tamper(self):
        for mutation in ['protected','symlink','missing','extra','caller','foreign','truncated','blob']:
            repo,head,sha,manifest,data=self.evidence();tree=data[f'repos/{repo}/git/trees/{"c"*40}?recursive=1']
            if mutation=='protected': tree['tree'][0]['sha']='e'*40
            if mutation=='symlink': tree['tree'][1]['mode']='120000'
            if mutation=='missing': tree['tree'].pop(0)
            if mutation=='extra': tree['tree'].append({'path':'injected','sha':'e'*40,'mode':'100644','type':'blob'})
            if mutation=='caller': tree['tree'][-1]['sha']='e'*40
            if mutation=='foreign': data[f'repos/{repo}/pulls/1']['base']['repo']['full_name']='upstream/labs'
            if mutation=='truncated': tree['truncated']=True
            if mutation=='blob': data[f'repos/{repo}/git/blobs/{tree["tree"][1]["sha"]}']['content']='bm9wZQ=='
            with tempfile.TemporaryDirectory() as d,self.subTest(mutation=mutation),self.assertRaises((grade.Invalid,grade.Unavailable)):
                grade.snapshot(data.__getitem__,repo,head,1,'preflight',sha,manifest,Path(d))

if __name__=='__main__': unittest.main()
