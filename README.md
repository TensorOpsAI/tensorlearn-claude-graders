# TensorLearn Claude workshop graders

Instructor-owned runtime and immutable GitHub Actions grader for the public
`TensorOpsAI/tensorlearn-claude-labs` exercise templates.

## Assessment model

AI work runs locally through each learner’s existing Claude login. GitHub Actions
receives no AI provider credential and makes no AI calls.

- Preflight checks bounded numeric JSON independently.
- Bug-fix compares 24 function cases against host-held expectations and reruns the
  same student regression tests on original and submitted source in isolated workers.
- Eleven labs submit a local `assessment.json`. The trusted grader validates exact
  source hashes, pinned rubric/revision, criterion IDs, boolean values and source
  quotations, then computes fixed points and critical caps.
- Skills, evals and incident also run 59 independent candidate-code cases across
  the three labs. Their grades combine report and functional checks equally.

The local report is learner-controlled evidence. It does not authenticate the
model, prove authorship, or mean CI observed a Claude session. Model judgment is
probabilistic; only the contract checks and score formula are deterministic.

See `VALIDATION.md` for actual checks and remaining acceptance. Live Claude tests
were explicitly deferred. Local practice, GitHub artifacts and a saved official
TensorLearn result are distinct.

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

## Standalone starters

Each exercise can also ship as its own repository, `TensorOpsAI/tensorlearn-<key>-lab`,
with the exercise directory at the repository root. Learners then fork one repository
per TensorLearn lab, so several labs can be open at once. The starter's caller passes
`layout: standalone`, and these submissions are graded by fixed rules only
(`fixed_rules.py`): no `assessment.json`, no local AI step.

- preflight and bug-fix keep their existing checks.
- skills, evals and incident score their functional cases alone (critical cases cap at 25).
- hooks, recurring-regressions and subagents run published cases in the Docker worker
  (plus student-test differentials for the first two).
- intent, review, release, spec-plan, model-routing and subagents check documents and
  JSON as data. intent, review and release add a short graded block to the task
  (`fixed_rules.TASK_ADDITIONS`); the others use their existing files and tables.
- Reference solutions are kept **outside** this public repository (learners can read the
  grader) in a private checkout; set `TENSORLEARN_SOLUTIONS` to it, default
  `../tensorlearn-claude-lab-solutions`. Each must score 100 and each untouched starter at most 25.

`python3 build_standalone.py --starter <labs checkout>` freezes the generated starter
files into `manifest.json` (`standalone`). After publishing the grader commit, add
`--out <dir> --grader-sha <sha>` to write the starter repositories with pinned callers.
The TensorLearn lab uses grader job `lab-grade`, artifact `lab-score` and the exercise
key as its folder name.

## Checking published templates

Three checks, from cheapest to closest to a real learner:

1. `RUN_DOCKER=1 python3 -m unittest discover -s tests`: every private reference solution
   goes through the real standalone path (PR snapshot of the generated
   starter, fixed rules, `score.json`) and must score full marks; untouched starters score at
   most a quarter; editing a protected file is invalid.
2. `python3 tools/verify_templates.py code-lab-templates.json`: compares each TensorLearn
   template (exported with `tools/export-templates.js`) with GitHub: starter commit, caller
   pin and exercise key, grader job/artifact, and starter files against the grader manifest.
3. `python3 tools/canary.py code-lab-templates.json`: the learner GitHub flow for real. It
   forks each starter, keeps one PR with the reference solution open inside the fork, pushes a
   new commit each run, waits for the grader run and checks the uploaded `score.json`. TensorLearn's own `/load` and `/submit`
   are shared by every lab; rehearse them once with a learner account.

## Local checks

Python 3.11+. Docker tests need the pinned worker image preloaded; with Colima, point
`TMPDIR` inside your home folder so the worker can mount it. `RUN_DOCKER=1` enables
the artifact-check Docker tests.

```
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s runtime/tests -v
python3 build_workflow.py
```

The restricted authoring launcher and MCP broker live in `runtime/`. Install this
release **outside** the learner checkout. Its local restrictions aid the exercise;
an unmanaged user can start a different client. They do not prove human authorship.
Candidate code runs only in the no-network, credential-free Docker worker.

## Local assessment and rehearsal

Use `runtime/assess.py` to produce the report from frozen source files through a
tool-free local Claude session. The authoring broker cannot write that report.
The intent coach cannot write the intent. The instructor configuration selects
approved full model IDs; the learner uses their normal local Claude sign-in.

Other local commands rehearse recurring regressions, scoped agents, isolated hook
probes, sanitized skills, fictional release/incident tools and bounded model stages.
The runtime documentation lists the precise boundaries and unrun checks. Their
traces are local evidence, not an authenticated provider ledger or a GitHub proof.
Optional CLI budget targets are not account-wide spending caps.

Never put provider credentials in exercise files, candidate workers, submitted logs,
GitHub workflows or template records. Candidate code runs only in credential-free,
no-network Docker workers. A reusable workflow does not inherit upstream secrets.

## Rebuilding

Change grader source on a review branch, run tests and regenerate the workflow.
Publish a new immutable grader commit, regenerate every starter caller from that
commit, publish a new starter revision, and rebuild the instructor records with
both full pins. Existing copied labs keep their selected release.

No organization, learner, membership, credential, attempt, session or result data
belongs in this repository.
