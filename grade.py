"""Trusted grading controller. Candidate modules run only in the isolated worker."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")

class Unavailable(RuntimeError):
    """Infrastructure or required assessment unavailable: never emit a score."""

class Invalid(ValueError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise Unavailable("Authenticated redirects are not allowed")

def api(path):
    request = urllib.request.Request('https://api.github.com/' + path, headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = response.read(4_000_001)
            if len(data) > 4_000_000 or 'rel="next"' in response.headers.get('Link',''):
                raise Unavailable('Incomplete GitHub evidence')
            return json.loads(data)
    except (OSError, ValueError) as error:
        raise Unavailable('GitHub evidence unavailable') from error

def blob_sha(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

def editable(path, patterns):
    # Deliberately support only exact paths and subtree /** patterns.
    return any(p == '**' or path == p or (p.endswith('/**') and path.startswith(p[:-2])) for p in patterns)

def allowed_edit(path,profile):
    protected=['exercise.json','practice.mjs','.git/**','.github/**','.mcp.json',
               '.claude/settings.json','.claude/settings.local.json','runtime/**','grader/**']
    return editable(path,profile['writeAllow']) and not editable(path,protected+profile.get('writeDeny',[]))

def caller(key, grader_sha, repo='TensorOpsAI/tensorlearn-claude-graders'):
    if not re.fullmatch('[a-z][a-z0-9-]*',key) or not SHA.fullmatch(grader_sha) or not REPO.fullmatch(repo):
        raise ValueError('Invalid release identity')
    return f'''name: Check {key}
on:
  pull_request:
    types: [opened, synchronize, reopened]
    branches: [main]
    paths:
      - 'exercises/{key}/**'
permissions:
  contents: read
  pull-requests: read
jobs:
  grade:
    uses: {repo}/.github/workflows/grade.yml@{grader_sha}
    with:
      grader-sha: {grader_sha}
      exercise-key: {key}
'''

def identity(pull, repo, head, number):
    if (pull.get('number') != number or pull.get('state') != 'open' or pull.get('merged_at')
        or pull.get('head',{}).get('sha') != head
        or pull.get('head',{}).get('repo',{}).get('full_name','').lower() != repo.lower()
        or pull.get('base',{}).get('repo',{}).get('full_name','').lower() != repo.lower()
        or pull.get('base',{}).get('ref') != 'main'):
        raise Invalid('Submit an open same-repository PR targeting main at the selected head')
    base = pull.get('base',{}).get('sha','')
    if not SHA.fullmatch(base): raise Unavailable('Missing PR base identity')
    return base

def snapshot(fetch, repo, head, number, key, grader_sha, manifest, destination):
    if not REPO.fullmatch(repo) or not SHA.fullmatch(head) or not SHA.fullmatch(grader_sha):
        raise Unavailable('Invalid run identity')
    profile = manifest['exercises'][key]
    prefix = f'exercises/{key}/'
    before = identity(fetch(f'repos/{repo}/pulls/{number}'), repo,head,number)
    expected = dict(manifest['files'])
    for other in manifest['exercises']:
        expected[f'.github/workflows/{other}.yml'] = blob_sha(caller(other,grader_sha).encode())
    # A valid final tree alone does not establish a valid PR diff: a learner could
    # alter main, then restore protected files in the PR. Freeze the complete base
    # tree to the release's hashes, including generated pinned callers. Comparing
    # tree contents avoids a circular dependency on the starter commit's own SHA.
    base_commit = fetch(f'repos/{repo}/git/commits/{before}')
    if base_commit.get('sha') != before:
        raise Unavailable('Base commit identity mismatch')
    base_tree_sha = base_commit.get('tree',{}).get('sha','')
    if not SHA.fullmatch(base_tree_sha):
        raise Unavailable('Missing base tree identity')
    base_tree = fetch(f'repos/{repo}/git/trees/{base_tree_sha}?recursive=1')
    if (base_tree.get('sha') != base_tree_sha or base_tree.get('truncated')
        or not isinstance(base_tree.get('tree'),list)):
        raise Unavailable('Incomplete base tree evidence')
    if len(base_tree['tree']) > 1500:
        raise Invalid('Base tree exceeds limit')
    baseline = {}
    for item in base_tree['tree']:
        if item.get('type') == 'tree': continue
        path = item.get('path','')
        if (not path or path.startswith('/') or any(p in ('','.','..') for p in path.split('/'))
            or path in baseline or item.get('type') != 'blob' or item.get('mode') != '100644'
            or not SHA.fullmatch(item.get('sha',''))):
            raise Invalid('PR base must contain only released regular files')
        baseline[path] = item['sha']
    if baseline != expected:
        raise Invalid('PR base differs from the released starter; restore main before submitting')
    commit = fetch(f'repos/{repo}/git/commits/{head}')
    if commit.get('sha') != head: raise Unavailable('Commit identity mismatch')
    tree_sha = commit.get('tree',{}).get('sha','')
    if not SHA.fullmatch(tree_sha): raise Unavailable('Missing tree identity')
    tree = fetch(f'repos/{repo}/git/trees/{tree_sha}?recursive=1')
    if tree.get('sha') != tree_sha or tree.get('truncated') or not isinstance(tree.get('tree'),list):
        raise Unavailable('Incomplete tree evidence')
    if len(tree['tree']) > 1500: raise Invalid('Tree exceeds limit')
    found = {}
    for item in tree['tree']:
        path = item.get('path','')
        if item.get('type') == 'tree': continue
        if (not path or path.startswith('/') or any(p in ('','.','..') for p in path.split('/'))
            or path in found or item.get('type') != 'blob' or item.get('mode') != '100644'
            or not SHA.fullmatch(item.get('sha',''))):
            raise Invalid('Only regular exercise files are accepted')
        found[path] = item
        allowed = path.startswith(prefix) and allowed_edit(path[len(prefix):],profile)
        if not allowed and expected.get(path) != item['sha']:
            raise Invalid('A protected file changed or an unexpected file was added')
    for path in expected:
        allowed = path.startswith(prefix) and allowed_edit(path[len(prefix):],profile)
        if path not in found and not allowed: raise Invalid('A protected file is missing')
    total = 0
    for path,item in found.items():
        if not path.startswith(prefix): continue
        if item.get('size',0) > 65536: raise Invalid('File exceeds 64 KiB')
        blob = fetch(f'repos/{repo}/git/blobs/{item["sha"]}')
        if blob.get('sha') != item['sha'] or blob.get('encoding') != 'base64':
            raise Unavailable('Invalid blob evidence')
        try: data=base64.b64decode(''.join(blob['content'].split()),validate=True)
        except (ValueError, KeyError) as e: raise Unavailable('Invalid blob bytes') from e
        if len(data)>65536 or blob.get('size') != len(data) or blob_sha(data)!=item['sha']:
            raise Unavailable('Blob hash/size mismatch')
        total += len(data)
        if total > 1_000_000: raise Invalid('Exercise exceeds 1 MB')
        target=destination/path[len(prefix):]
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    after=identity(fetch(f'repos/{repo}/pulls/{number}'),repo,head,number)
    if after!=before: raise Unavailable('PR changed during fetch; rerun selected head')
    return profile

def strict_json(data):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise ValueError('Duplicate keys')
            out[k]=v
        return out
    return json.loads(data,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite')))

def arithmetic(workspace):
    try:
        data=(workspace/'answer.json').read_bytes()
        if len(data)>1024: raise ValueError('Too large')
        value=strict_json(data.decode('utf-8'))
        if not isinstance(value,dict) or set(value)!={'answer'} or type(value['answer']) not in (int,float):
            raise ValueError('Expected one numeric answer')
        if isinstance(value['answer'],float) and not math.isfinite(value['answer']):
            raise ValueError('Expected finite answer')
    except (OSError,ValueError,UnicodeError) as e: raise Invalid('Expected bounded JSON with one finite numeric answer') from e
    return [{'id':'addition','passed':value['answer']==2}]

def isolated_checks(workspace,key,image):
    if key == 'bug-fix':
        import bug_assessment
        try:
            return bug_assessment.assess(workspace,image)
        except bug_assessment.Unavailable as error:
            raise Unavailable(str(error)) from error
    # Never accept candidate-produced pass/fail logs for missing AI assessments.
    raise Unavailable('Behavioral assessment is not released for this lab')


def score(key,head,grader_sha,checks,invalid=False,weighted_points=None):
    maximum=10 if key=='preflight' else 100
    passed=sum(c['passed'] for c in checks)
    points=0 if invalid else maximum*passed//max(1,len(checks))
    if weighted_points is not None and not invalid:
        if type(weighted_points) is not int or not 0<=weighted_points<=maximum:
            raise Unavailable('Invalid trusted weighted score')
        points=weighted_points
    if key=='bug-fix' and not invalid:
        outcomes={c['id']:c['passed'] for c in checks}
        critical=['exact-owner','trim-identifiers','duplicate-id-other-owner','prefix-owner','empty-owner','whitespace-owner']
        if not all(outcomes.get(k,False) for k in critical): points=min(points,25)
        if not outcomes.get('student-regression-differential',False): points=min(points,70)
    return {'schema':1,'exercise':key,'grader_sha':grader_sha,'submission_sha':head,
            'status':'invalid' if invalid else 'graded','score':points,'max':maximum,
            'points':{'scope':0,'static':0,'tests':points,'regression':0},
            'gates':{'tests':{'total':max(1,len(checks)),'passed':0 if invalid else passed}}}

def main():
    if os.environ.get('SUBMISSION_EVENT')!='pull_request': raise Unavailable('Only PR events accepted')
    key=os.environ['EXERCISE_KEY']; head=os.environ['SUBMISSION_SHA']; grader_sha=os.environ['GRADER_SHA']
    manifest=json.loads((ROOT/'manifest.json').read_text())
    if key not in manifest['exercises']: raise Unavailable('Unknown exercise')
    # Release eligibility is instructor-owned. No partial stand-in for missing AI behavior.
    if key not in manifest['releaseReady']: raise Unavailable('This template is a draft; assessment is not released')
    with tempfile.TemporaryDirectory() as temp:
        workspace=Path(temp); workspace.chmod(0o755)
        try:
            profile=snapshot(api,os.environ['SUBMISSION_REPO'],head,int(os.environ['SUBMISSION_PR']),key,grader_sha,manifest,workspace)
            weighted=None
            if key in manifest.get('localAIReports',[]):
                import report_assessment
                try:
                    rubric_bytes=(ROOT/'rubrics'/f'{key}.json').read_bytes()
                    checks,weighted=report_assessment.assess(workspace,key,grader_sha,rubric_bytes)
                    if key in ('skills','evals','incident'):
                        import artifact_checks
                        try:
                            functional=artifact_checks.assess(workspace,key,manifest['workerImage'])
                        except artifact_checks.Unavailable as error:
                            raise Unavailable(str(error)) from error
                        ceiling=100
                        outcomes={c['id']:c['passed'] for c in checks}
                        for criterion in json.loads(rubric_bytes)['criteria']:
                            if not outcomes[criterion['id']]:ceiling=min(ceiling,criterion.get('criticalCap',100))
                        critical=set(artifact_checks.critical_case_ids(key))
                        if any(c['id'] in critical and not c['passed'] for c in functional):ceiling=min(ceiling,25)
                        weighted=min(ceiling,weighted//2+50*sum(c['passed'] for c in functional)//len(functional))
                        checks += [{'id':'functional:'+c['id'],'passed':c['passed']} for c in functional]
                except report_assessment.Invalid as error:
                    raise Invalid(str(error)) from error
            else:
                checks=arithmetic(workspace) if key=='preflight' else isolated_checks(workspace,key,manifest['workerImage'])
            record=score(key,head,grader_sha,checks,weighted_points=weighted)
        except Invalid as e:
            checks=[{'id':'submission-contract','passed':False}]
            record=score(key,head,grader_sha,checks,invalid=True)
            print(str(e))
    out=Path('result'); out.mkdir(exist_ok=True)
    (out/'score.json').write_text(json.dumps(record)+'\n')
    print(json.dumps({'status':record['status'],'score':record['score'],'checks':checks}))

if __name__=='__main__': main()
