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
