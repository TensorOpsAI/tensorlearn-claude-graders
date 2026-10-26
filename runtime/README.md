# Local Claude authoring and assessment

This runtime is installed from the pinned instructor grader release **outside** the
learner checkout. It is separate from the TensorLearn submission connector.

The launcher verifies its release hashes and Claude Code 2.1.310, loads instructor
profiles, retains project guidance, turns off auto memory, and starts a fresh
session with `Read,Glob,Grep`. Native shell, edit, network and agent tools are absent.
The instructor MCP server offers only scoped file replacement and named practice
checks. The intent exercise has no MCP servers or mutation tools.
The broker starts Python in isolated mode so learner files cannot shadow its imports.

`apply_patch` takes `{path,content}` and atomically replaces the entire UTF-8 file.
It rejects path escapes, symlinks, protected infrastructure and files above 64 KiB.
The normal project correction exercise permits broad edits with infrastructure
excluded; its launcher prompt gives no root-cause hint.

`run_tests` takes `{testId}`. It never accepts a command. Candidate programs run
only in a disposable Docker worker with no network, host credentials, root user,
host home or Docker socket. Workspace snapshots are read-only; output, duration,
memory, process count and temporary storage are bounded. Missing Docker/image
configuration is an explicit infrastructure error. No image is pulled implicitly.

## Instructor preparation

1. Review and pin this grader commit. Install it outside learner working files.
2. Install Claude Code 2.1.310. No automatic install/update is performed.
3. Select the permitted full versioned Claude model IDs and a reviewed Node worker
   image at its immutable digest. Pull the image explicitly before the workshop.
4. Save a configuration file outside the learner checkout with exactly two fields:
   `models` (a list of full versioned IDs) and `workerImage` (the digest-pinned image,
   or `null` if practice execution is unavailable). This file contains no API key.
5. Validate a session without spending model tokens:

```sh
python3 /path/to/pinned-grader/runtime/launch.py \
  --exercise preflight \
  --workspace /path/to/student-fork/exercises/preflight \
  --configuration /path/to/instructor-config.json \
  --model "$INSTRUCTOR_SELECTED_FULL_MODEL_ID" --check
```

6. Remove `--check` to begin interactive authoring using the student's normal
   Claude authentication. The configured model must be available to that account.
7. End authoring. For the eleven rubric-based exercises, generate the local
   assessment described below using the same student's Claude login.
8. Inspect changes, then use the separate TensorLearn connector
   to commit/push and explicitly submit the selected PR head. This launcher does
   not grant GitHub access, push, submit, or manufacture an official grade.

## Local assessment

All AI calls use the student's existing local Claude login. No instructor AI
gateway or provider secret is required by GitHub. Candidate code remains in the
credential-free Docker worker; only the trusted Claude process uses the login.

```sh
python3 /path/to/pinned-grader/runtime/assess.py \
  --exercise intent \
  --workspace /path/to/student-fork/exercises/intent \
  --configuration /path/to/instructor-config.json \
  --model "$INSTRUCTOR_SELECTED_FULL_MODEL_ID" \
  --grader-revision "$PUBLISHED_GRADER_COMMIT"
```

Add `--check` to validate the source inventory, rubric and CLI without calling a
model. A real assessment starts a fresh, tool-free Claude print session with a
fixed JSON schema and pinned questions. It disables project customizations for
that evaluation, treats source instructions as data, and retains normal Claude
authentication. It validates every boolean answer and exact quotation before
writing `assessment.json`; a failed model call or malformed response creates no
new report. Reassess after any source edit. The authoring broker cannot edit this
report, including in the broad recurring-regression profile.

The report contains source hashes, rubric digest, declared full model ID and
grader revision. GitHub validates those fields and quotations and computes the
published points and critical caps. It runs no model. This is a **local AI report**:
the learner controls the report, and hashes do not authenticate who generated it,
which model ran, or whether a quotation semantically supports an answer. Boolean
judgments remain model judgments; only report validation and scoring arithmetic
are deterministic. There is no human-authorship or provider attestation claim.

## Fresh-session rehearsal

The recurring-regression controller runs two differently worded tasks in separate
disposable copies, preserving normal project guidance and disabling auto memory.
It uses the student's own login, scoped authoring tools and fresh processes without
resume/continue. Independent instructor checks then run in Docker to verify the
current customer-code and cache contracts and the newly requested helper. Original
exercise files remain unchanged. Store the trace outside the learner checkout:

```sh
python3 /path/to/pinned-grader/runtime/replay.py \
  --workspace /path/to/student-fork/exercises/recurring-regressions \
  --configuration /path/to/instructor-config.json \
  --model "$INSTRUCTOR_SELECTED_FULL_MODEL_ID" \
  --output /path/to/new-local-rehearsal-directory
```

`--check` verifies setup without calling a model. A trace records local observations,
not an authenticated grading statement. The GitHub report does not independently
certify these fresh sessions. Read `VALIDATION.md` for which execution boundaries
were tested and which actual Claude calls remain unrun.

The other local rehearsal entrypoints are:

| Exercise | Command | Capability boundary |
| --- | --- | --- |
| Subagents | `behavior_replay.py --exercise subagents` | Named native roles, separate role MCP inventories/context, coordinator cannot implement, model overrides denied. |
| Hooks | `behavior_replay.py --exercise hooks` | Fixed write/command probe IDs; submitted guard runs in a credential-free Docker child. |
| Skills | `behavior_replay.py --exercise skills` | Sanitized skill-only plugin, fixed message/checker tools, candidate checker in Docker. |
| Release | `simulate.py --exercise release --scenario degraded --phase release` | Fictional staging only; server-owned exact-version approvals and health/rollback state. |
| Incident | `simulate.py --exercise incident --scenario sustained-rise` | Deterministic detector selects read/propose capabilities; no deployment tools. |

Pass `--workspace`, `--configuration`, `--model` and an external `--output` path
to each command. `behavior_replay.py` expects a new trace file; `simulate.py`
expects an output directory. Add `--check` for configuration-only validation.
These commands retain the local-unattested evidence boundary described above.

## Local model-routing rehearsal

`route.py` executes the submitted routes using the student's own Claude login.
Copy `routing-policy.example.json` outside the learner checkout, map its model
aliases to full IDs permitted by the normal instructor configuration, and choose
the call/input/output/retry ceilings. The example's numeric values are editable
practice ceilings; no monetary allowance is fixed by this release.

```sh
python3 /path/to/pinned-grader/runtime/route.py \
  --workspace /path/to/student-fork/exercises/model-routing \
  --configuration /path/to/instructor-config.json \
  --policy /path/to/instructor-routing-policy.json \
  --output /path/to/new-local-routing-trace.json
```

The controller validates every alias/stage, serializes requests, counts failed
requests toward the call allowance, bounds retries, and permits at most one
instructor-approved escalation from extract to reason for each task. Every stage
is fresh and tool-free, with an empty MCP configuration. It sets the pinned CLI's
output-token limit and disables client retries so retries remain in the controller.
It records input hashes and reported model/usage fields and rejects unexpected
reported models or output overruns.

Supplied task/context is conservatively bounded by UTF-8 byte count against the
input allowance; Claude's wrapper/schema overhead is additional. The optional
`clientBudgetTargetUsd` is passed to the CLI circuit breaker and stops later
admission when reported cost exhausts it. Missing accounting stops further
target-based requests. The target may overshoot and is **not** a hard financial
cap, an account-wide allowance, or an invoice. Local Claude usage fields remain
reported observations rather than independently authenticated provider evidence.

## Boundaries and validation

This is a local learning aid. A user who controls the laptop can bypass or edit
the launcher, and project guidance remains untrusted input. The hash lock detects
accidental/runtime-file changes relative to the selected release; it is not a
signature or protection against a user who replaces both files and hashes.
The selected grader release owns the tools/rubrics, but an unmanaged local user can
replace the release or run another Claude session. Authoring and assessment are
separate commands. `launch.py --mode replay` directs users to the exercise-specific
rehearsal commands instead of silently granting broader authoring tools.

The GitHub artifact assessment grades routing/context design; the separate local
route controller records the rehearsal. No local spend target is advertised as a
hard financial cap. Local practice, local traces and learner-written reports do
not become independent GitHub model observations merely by being submitted.

Tests cover scope escapes, symlinks, hardlinks, readonly intent, policy tampering,
MCP inventory, fixed command selection, environment filtering and resource flags.
Docker command assertions use mocks and do not prove actual container isolation.
The separately executed `runtime/tests/integration_worker.py` probe verifies real
Docker behavior for the configured image: non-root user, missing host credential
variables, readonly workspace/system files, no Docker socket, denied outbound
connection, unlisted-command rejection and correctly reported failing practice.
Live Claude runs are deferred at the user's request. `--check` verifies
version/help/configuration only. Transport mocks are labelled in the tests.

```sh
python3 -m unittest discover -s runtime/tests -v
```

After an instructor source/profile edit, run `python3 runtime/seal_release.py`
before committing the next immutable grader release. Learners must not reseal
their installed release to bypass a mismatch.
