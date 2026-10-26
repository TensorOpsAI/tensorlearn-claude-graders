# Release validation

The release contains thirteen public starter templates, a restricted local Claude
runtime and a single-job GitHub grader. All material is fictional.

## Passed

- 45 grader tests, including real Docker executions of known-correct and deliberately
  broken bug-fix, skills, evals and incident implementations. No failures or skips
  in `RUN_DOCKER=1 python3 -m unittest discover -s tests -v`.
- 116 runtime tests for policy integrity, canonical paths, symlink/hardlink escape
  rejection, model selection, source/evidence validation, role tool separation,
  hook decisions, sanitized skill loading, routing bounds and simulated approval/
  incident controls. Claude transport in these tests is mocked, not a paid model.
- Real Docker boundary probe: non-root worker, no candidate access to host secrets,
  GitHub/Anthropic environment, host filesystem, Docker socket or network; read-only
  workspace; expected failing candidate returns a failed local practice result.
- Twelve unfinished starter practice commands execute in the isolated worker and
  fail as intended. The intent profile exposes no mutation/test MCP tools.
- All eleven assessment configurations and local replay configurations pass the
  installed Claude Code 2.1.310 `--check` path without model calls. Fresh-session
  recurring-regression functional probes accept the reference and reject the old
  customer-code behavior in real Docker.
- Starter JSON/JavaScript syntax and metadata checks, including the hidden-diagnosis
  student brief. The public grader necessarily exposes its rubric, while student
  instructions do not disclose the recurring-regression cause.
- Generated workflow parses as YAML, contains exactly one `lab-grade` job, recreates
  the exact trusted source bytes, and keeps each embedded run script below 21,000
  characters. It uses no candidate checkout or AI provider secret.

## Assessment boundaries

Preflight independently checks arithmetic. Bug-fix independently checks 24 function
cases and the same student test suite against original and submitted source.
Differential test behavior does not prove authorship or when the test was written.

Eleven labs use a learner-local Claude report. GitHub checks immutable source hashes,
exact quoted evidence, criterion IDs/boolean types, rubric revision and arithmetic.
It does not independently verify semantic judgment or model identity. Skills,
evals and incident additionally receive 19, 13 and 27 independent code cases.
Report/functional weighting and critical caps are published in the starter README.

The local controllers implement scoped Agent sessions, isolated guard probes,
sanitized skill rehearsal, recurring fresh sessions, model stages and fictional
release/incident tools. Configuration/unit tests do not prove actual Claude
invocation/activation behavior. Optional local budget targets do not cap the whole
Claude account. GitHub does not use student or instructor AI credentials.

## Deferred or not yet demonstrated

- Live Claude calls and behavioral rehearsals were explicitly deferred by the user;
  the local CLI is signed out. No model inference or paid AI run is claimed.
- Actual GitHub workflow runs and artifacts are recorded separately when observed.
  Local tests alone do not prove the GitHub App fetched or saved an official result.
- Real learner enrollment, explicit TensorLearn submission, saved score and reload
  still require a live delivery rehearsal.
- Native TensorLearn publication needs the deployed template API and a scoped Academy
  key. A public GitHub repository is not a saved platform template record.

Initial local checks caught nonfinite numeric input handling, modified PR baselines,
and a sandbox-only Docker connection failure; these were fixed or rerun with Docker
access before the successful results above.
