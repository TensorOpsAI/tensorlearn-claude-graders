"""Freeze reviewed starter files into the trusted grader manifest, without circular pins."""
import argparse
import json
from pathlib import Path
from grade import blob_sha
from build_workflow import render

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--starter',type=Path,required=True)
    parser.add_argument('--worker-image',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    exercises={}
    for path in sorted((args.starter/'exercises').glob('*/exercise.json')):
        item=json.loads(path.read_text())
        if path.parent.name!=item['key']: raise ValueError('Exercise directory/key mismatch')
        exercises[item['key']]=item
    if len(exercises)!=13: raise ValueError('Expected thirteen exercises')
    files={}
    for path in sorted(args.starter.rglob('*')):
        relative=path.relative_to(args.starter).as_posix()
        if any(part in ('.git','__pycache__') for part in path.relative_to(args.starter).parts): continue
        if path.is_symlink(): raise ValueError('No starter symlinks')
        if not path.is_file() or relative.startswith('.github/workflows/'): continue
        if path.stat().st_mode & 0o111: raise ValueError(f'Unexpected executable mode: {relative}')
        files[relative]=blob_sha(path.read_bytes())
    reports=[p.stem for p in sorted((root/'rubrics').glob('*.json'))]
    if set(reports)!=set(exercises)-{'preflight','bug-fix'}: raise ValueError('Expected eleven local-report rubrics')
    (root/'manifest.json').write_text(json.dumps({'schema':1,'releaseReady':list(exercises), 'localAIReports':reports,
        'workerImage':args.worker_image,'exercises':exercises,'files':files},indent=2)+'\n')
    path=root/'.github/workflows/grade.yml';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(render())

if __name__=='__main__':main()
