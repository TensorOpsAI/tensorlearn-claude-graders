"""Build one-exercise starter repositories and freeze their files into the manifest.

Each exercise in the monorepo starter becomes its own repository, so a learner forks
one repository per lab. The exercise directory becomes the repository root, with a
TensorLearn submission guide and a pinned caller. Standalone starters are graded by
fixed rules only (see fixed_rules.py); they never use a local assessment report.
"""
import argparse
import json
from pathlib import Path
from grade import blob_sha, caller
import fixed_rules

ROOT=Path(__file__).resolve().parent
OMIT={'practice.mjs'}  # Launcher-only practice packs; standalone starters have no launcher.
LOCAL_SECTIONS=('## Local assessment and submission','## Run and submit')

def guide(key,item):
    checks=fixed_rules.descriptions(key)
    graded='\n'.join(f'- {text}' for text in checks)
    return f'''## How to submit

1. Fork this repository to your personal GitHub account. Use fictional information
   only, keep the fork public, and keep `main` as its default branch.
2. Clone your fork and enable GitHub Actions in its **Actions** tab. Use **Connect
   GitHub for scoring** on the lab page to install the read-only grading App on your
   fork. Return to the lab and use **Recheck connection** if needed.
3. Download the TensorLearn client from the lab page and extract it outside your fork.
   Follow the page's connection command from your fork folder. Paste your learner
   token only into the hidden terminal prompt. Run `/load` from your fork folder to
   connect the repository. Run `/load` again after the facilitator launches the round.
4. Create a branch such as `{key}/first-draft` and complete the task above. Change
   only the files the task allows; any other change makes the submission invalid.
5. Commit and push your branch. Open a PR **inside your own fork**, targeting its
   `main` branch. GitHub may suggest the upstream repository: select your fork instead.
   Opening or updating this PR starts the checks automatically.
6. Keep the PR open. When ready, run **`/submit`** in the connected Claude Code session
   to submit that exact pushed revision for the current round. **No merge is required.**
7. Use `/check` or refresh the lab page to read the saved result. The Actions run and
   `lab-score/score.json` are useful feedback, but an Actions check alone does not
   submit your work or prove that the server saved a score.

A change to your answer requires a new commit, push, and explicit `/submit`. Keep
`main` unchanged: the grader compares your PR with the released starter.

## What is graded

The pinned grader in `.github/workflows/grade.yml` reads your files as data and runs
your code only in an isolated, offline container. Every check is a fixed rule; no AI
model judges your work. Your facilitator separately reviews clarity and reasoning.

{graded}
'''

CLAUDE_MD='''# TensorLearn exercise

Read @README.md for the task, the files you may change and how the work is graded.

Help the learner reason and work within the allowed files. Do not silently commit,
push, open or merge a PR. The learner opens a PR inside their own fork, targeting
that fork's main branch. Opening or updating the PR starts advisory checks. Only the
learner's explicit /submit request submits the selected pushed revision to
TensorLearn. Keep the PR open; no merge is required. Never present an Actions check
as a saved official score.
'''

def starter_files(starter,key):
    """Every file of one standalone starter except its pinned caller, as bytes."""
    source=starter/'exercises'/key
    item=json.loads((source/'exercise.json').read_text())
    files={}
    for path in sorted(source.rglob('*')):
        relative=path.relative_to(source).as_posix()
        if path.is_symlink(): raise ValueError('No starter symlinks')
        if not path.is_file() or relative in OMIT or relative=='README.md': continue
        if path.stat().st_mode & 0o111: raise ValueError(f'Unexpected executable mode: {relative}')
        files[relative]=path.read_bytes()
    readme=(source/'README.md').read_text()
    cuts=[readme.index(h) for h in LOCAL_SECTIONS if h in readme]
    if not cuts: raise ValueError(f'{key}: README has no submission section to replace')
    addition=fixed_rules.TASK_ADDITIONS.get(key,'')
    files['README.md']=(readme[:min(cuts)].rstrip()+'\n\n'+(addition+'\n' if addition else '')+guide(key,item)).encode()
    # recurring-regressions ships CLAUDE.md as exercise material; keep the learner's file.
    files.setdefault('CLAUDE.md',CLAUDE_MD.encode())
    files.setdefault('.gitignore',(starter/'.gitignore').read_bytes())
    return files

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--starter',type=Path,required=True,help='checkout of tensorlearn-claude-labs')
    parser.add_argument('--out',type=Path,help='write starter repositories here (needs --grader-sha)')
    parser.add_argument('--grader-sha',help='published grader commit for the pinned callers')
    args=parser.parse_args()
    manifest_path=ROOT/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    keys=[key for key in sorted(manifest['exercises']) if key in fixed_rules.DESCRIPTIONS]
    starters={key:starter_files(args.starter,key) for key in keys}
    if args.out:
        if not args.grader_sha: parser.error('--out needs --grader-sha')
        for key,files in starters.items():
            files=dict(files)
            repo=manifest.get('graderRepo','TensorOpsAI/tensorlearn-claude-graders')
            files['.github/workflows/grade.yml']=caller(key,args.grader_sha,repo,standalone=True).encode()
            for relative,data in files.items():
                target=args.out/f'tensorlearn-{key}-lab'/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
        return
    manifest['standalone']={key:{p:blob_sha(d) for p,d in files.items()} for key,files in starters.items()}
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')

if __name__=='__main__':main()
