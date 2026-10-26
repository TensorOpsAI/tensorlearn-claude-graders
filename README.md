# TensorLearn Claude workshop graders

Instructor-owned runtime and immutable GitHub Actions grader for the public
`TensorOpsAI/tensorlearn-claude-labs` exercise templates.

## Release status

**Preflight** and **bug-fix** have implemented deterministic assessments. Preflight
reads the selected PR's immutable GitHub tree/blobs and checks the answer as data.
It never checks out or executes learner code. Bug-fix compares candidate function outputs in a no-network Docker worker against instructor expectations, and runs the same student tests against original and submitted source. This verifies differential behavior, not authorship or when the tests were written. Other exercises are **drafts**:
their starter materials and local authoring profiles do not establish completed
behavioral grading. Their workflows fail explicitly without a score.

See `VALIDATION.md` for actual checks and outstanding acceptance. Local practice,
GitHub score artifacts, and a saved official TensorLearn result are distinct.

## Immutable release

`manifest.json` freezes every protected starter file and the per-exercise edit
scope. Callers reference this repository's `.github/workflows/grade.yml` at a full
commit SHA. Each workflow contains exactly one `lab-grade` job and uploads the
single `lab-score/score.json` artifact. The reusable workflow embeds the trusted
Python source and manifest; it does not load code from a participant checkout.

An accepted PR stays inside the learner's fork, targets its unchanged starter
`main`, and selects the exact head. Do not merge exercise solutions into the
starter baseline. Forks can retain separate solution branches for separate labs.
Every other exercise and every workflow must remain unchanged.

## Local checks

Python 3.11+:

```
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s runtime/tests -v
python3 build_workflow.py
```

The restricted authoring launcher and MCP broker live in `runtime/`. Install this
release **outside** the learner checkout. Its local restrictions aid the exercise;
an unmanaged user can start a different client. They do not prove human authorship.
Candidate code runs only in the no-network, credential-free Docker worker.

## AI-dependent assessments

Do not put provider credentials in a public exercise, student fork, candidate
process, submitted log or template record. A reusable workflow called from a fork
does not inherit upstream repository secrets. The proposed semantic evaluators,
fresh-session agent replays, controlled subagent traces and provider usage checks
need an instructor-operated authenticated gateway/controller. Its provisioning,
funding and credential are not yet configured. No keyword rubric or student-written
trace is substituted for that assessment.

`isolated_checks` deliberately rejects unreleased AI behavioral assessment. Enabling
a manifest key is not implementation. Keep such TensorLearn records as drafts with
null grading until their real positive/negative acceptance is demonstrated.

## Rebuilding

Change grader source on a review branch, run tests and regenerate the workflow.
Publish a new immutable grader commit, regenerate every starter caller from that
commit, publish a new starter revision, and rebuild the instructor records with
both full pins. Existing copied labs keep their selected release.

No organization, learner, membership, credential, attempt, session or result data
belongs in this repository.
