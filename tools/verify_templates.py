"""Check TensorLearn code-lab templates against the GitHub repositories they reference.

For every GitHub Actions exercise in the given templates or labs, this confirms that:
- the starter repository is public and the pinned starter commit exists (and notes when
  `main` has moved past it);
- the starter's .github/workflows/grade.yml at that commit is exactly the standalone caller
  for the template's grader repository, grader commit and exercise key;
- the grader commit exists, its workflow defines the template's job and score artifact, and
  its manifest knows the exercise;
- every other starter file at that commit matches the grader manifest, so an untouched
  learner fork passes the grader's base-tree check.

Input: JSON exported from TensorLearn, a list of {"name", "definition": {"exercises": [...]}}.
Get it with tools/export-templates.js in a signed-in browser console, or pass --api to read
templates with an Academy API key (code-labs.templates.read) from $TENSORLEARN_KEY.
GitHub is read through the `gh` CLI. Nothing is written anywhere.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grade import blob_sha, caller  # noqa: E402

def gh(path):
    result=subprocess.run(['gh','api',path],capture_output=True,text=True)
    if result.returncode: raise LookupError(result.stderr.strip() or path)
    return json.loads(result.stdout)

def content(repo,path,ref):
    return base64.b64decode(gh(f'repos/{repo}/contents/{path}?ref={ref}')['content'])

def from_api(base):
    key=os.environ.get('TENSORLEARN_KEY')
    if not key: sys.exit('Set TENSORLEARN_KEY to an Academy API key with code-labs.templates.read.')
    def get(path):
        request=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+key})
        with urllib.request.urlopen(request,timeout=30) as response: return json.load(response)
    items=[]
    for template in get('/api/learn/code-lab-templates')['templates']:
        version=template.get('latestVersion')
        if version is None or template.get('archived'): continue
        detail=get(f"/api/learn/code-lab-templates/{template['id']}/versions/{version}")['version']
        items.append({'name':f"{template['title']} v{version}",'definition':detail['definition']})
    return items

def check_exercise(exercise):
    """Return a list of problems for one exercise definition (empty when it is consistent)."""
    grading=exercise.get('grading') or {}
    if grading.get('kind')!='github-actions': return []
    problems=[]
    key=grading['exerciseKey']; grader=grading['graderRepo']; sha=grading['graderSha']
    starter=exercise['participant']['starter']; url=starter['url']; revision=starter['revision']
    repo='/'.join(url.rstrip('/').removesuffix('.git').split('/')[-2:])
    try:
        info=gh(f'repos/{repo}')
        if info.get('private'): problems.append(f'starter {repo} is private; learners need a public fork')
        if info.get('archived'): problems.append(f'starter {repo} is archived')
        head=gh(f"repos/{repo}/commits/{info['default_branch']}")['sha']
        if head!=revision: problems.append(f'note: {repo} main is at {head[:7]}, the template pins {revision[:7]}')
        tree=gh(f'repos/{repo}/git/trees/{revision}?recursive=1')
    except LookupError as error:
        return problems+[f'starter {repo}@{revision[:7]} unreadable: {error}']
    files={item['path']:item['sha'] for item in tree['tree'] if item['type']=='blob'}
    expected_caller=caller(key,sha,grader,standalone=True).encode()
    if files.get('.github/workflows/grade.yml')!=blob_sha(expected_caller):
        problems.append('starter grade.yml is not the standalone caller for this grader, commit and key')
    try:
        workflow=content(grader,'.github/workflows/grade.yml',sha).decode()
        manifest=json.loads(content(grader,'manifest.json',sha))
    except LookupError as error:
        return problems+[f'grader {grader}@{sha[:7]} unreadable: {error}']
    if f"\n  {grading['graderJob']}:\n" not in workflow and f"name: {grading['graderJob']}\n" not in workflow:
        problems.append(f"grader workflow has no job {grading['graderJob']!r}")
    if f"name: {grading['scoreArtifact']}\n" not in workflow:
        problems.append(f"grader workflow uploads no artifact {grading['scoreArtifact']!r}")
    if manifest.get('graderRepo','TensorOpsAI/tensorlearn-claude-graders')!=grader:
        problems.append(f"grader commit names {manifest.get('graderRepo')} as its repository, not {grader}")
    frozen=manifest.get('standalone',{}).get(key)
    if frozen is None:
        problems.append(f'grader manifest has no standalone starter for {key!r}')
    else:
        others={p:s for p,s in files.items() if p!='.github/workflows/grade.yml'}
        if others!=frozen:
            changed=sorted(set(others)^set(frozen) | {p for p in set(others)&set(frozen) if others[p]!=frozen[p]})
            problems.append('starter files differ from the grader manifest: '+', '.join(changed[:8]))
    return problems

def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('export',nargs='?',type=Path,help='JSON exported from TensorLearn')
    parser.add_argument('--api',metavar='URL',help='read templates from this TensorLearn site instead')
    args=parser.parse_args()
    if not args.export and not args.api: parser.error('give an export file or --api')
    items=from_api(args.api) if args.api else json.loads(args.export.read_text())
    failed=0
    for item in items:
        for exercise in item['definition']['exercises']:
            problems=check_exercise(exercise)
            errors=[p for p in problems if not p.startswith('note:')]
            failed+=bool(errors)
            print(('FAIL ' if errors else 'ok   ')+f"{item['name']} / {exercise['id']}")
            for problem in problems: print('     - '+problem)
    print(f'\n{failed} exercise(s) with problems.')
    sys.exit(1 if failed else 0)

if __name__=='__main__': main()
