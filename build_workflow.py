"""Generate one trusted reusable job; candidate code is never checked out on the host."""
from pathlib import Path
import base64
ROOT=Path(__file__).resolve().parent

def render():
    # Sources are embedded at build time, so a caller cannot substitute grader files.
    sources={name:(ROOT/name).read_bytes() for name in ('grade.py','bug_assessment.py','artifact_checks.py','report_assessment.py','fixed_rules.py','manifest.json')}
    for path in sorted((ROOT/'rubrics').glob('*.json')):
        sources[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    staging=''
    for name,data in sources.items():
        for index,offset in enumerate(range(0,len(data),8192)):
            encoded=base64.b64encode(data[offset:offset+8192]).decode()
            mode='wb' if index==0 else 'ab'
            script=f'from pathlib import Path\nimport base64\np=Path({name!r})\np.parent.mkdir(parents=True,exist_ok=True)\nwith p.open({mode!r}) as f: f.write(base64.b64decode({encoded!r}))\n'
            staging+=f'      - name: Stage trusted {name} part {index+1}\n        shell: python\n        run: |\n'
            staging+=''.join('          '+line+'\n' for line in script.splitlines())
    return '''name: TensorLearn workshop grader
on:
  workflow_call:
    inputs:
      exercise-key:
        required: true
        type: string
      grader-sha:
        required: true
        type: string
      layout:
        required: false
        type: string
        default: monorepo
permissions:
  contents: read
  pull-requests: read
  id-token: write
jobs:
  lab-grade:
    name: lab-grade
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - name: Preload isolated worker for code assessment
        if: contains(fromJSON('["bug-fix","skills","evals","incident","hooks","recurring-regressions","subagents"]'), inputs.exercise-key)
        run: docker pull node:22.18.0-alpine@sha256:1b2479dd35a99687d6638f5976fd235e26c5b37e8122f786fcd5fe231d63de5b
'''+staging+'''      - name: Grade immutable submission
        shell: python
        env:
          GITHUB_TOKEN: ${{ github.token }}
          SUBMISSION_REPO: ${{ github.repository }}
          SUBMISSION_SHA: ${{ github.event.pull_request.head.sha }}
          SUBMISSION_PR: ${{ github.event.pull_request.number }}
          SUBMISSION_EVENT: ${{ github.event_name }}
          EXERCISE_KEY: ${{ inputs.exercise-key }}
          GRADER_SHA: ${{ inputs.grader-sha }}
          LAB_LAYOUT: ${{ inputs.layout }}
        run: |
          import runpy
          import sys
          from pathlib import Path
          sys.path.insert(0, str(Path.cwd()))
          runpy.run_path('grade.py', run_name='__main__')
      - name: Upload score
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: lab-score
          path: result/score.json
          if-no-files-found: error
          retention-days: 7
      - name: Report score to TensorLearn
        continue-on-error: true
        env:
          REPORT_URL: https://tensorops.ai/api/github/report
          AUDIENCE: https://tensorops.ai/code-labs
        run: |
          token=$(curl -sSf -H "Authorization: Bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \\
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=$AUDIENCE" | jq -r .value)
          echo "::add-mask::$token"
          for wait in 2 4 8 16 32; do
            status=$(curl -sS -o "$RUNNER_TEMP/report.txt" -w '%{http_code}' -X POST "$REPORT_URL" \\
              -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \\
              --data-binary @result/score.json || echo 000)
            echo "report: HTTP $status $(cat "$RUNNER_TEMP/report.txt")"
            case "$status" in
              2*) exit 0 ;;
              429|5*|000) sleep "$wait" ;;
              *) exit 1 ;;
            esac
          done
          exit 1
'''

if __name__=='__main__':
    path=ROOT/'.github/workflows/grade.yml';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(render())
