# Local runtime verification

Verified 26 October 2026 on macOS with Python 3.14.8, Claude Code 2.1.310 and
Docker. This records local evidence, not a TensorLearn saved learner score.

- **Passed:** 116 unit/configuration tests across authoring, assessment, recurring
  rehearsal, role/hook/skill rehearsal, simulations and routing. Claude transport
  is mocked in execution tests. Coverage includes path/size/symlink/hardlink boundaries; protected-case
  aliases; atomic replacement; bounded output/time; MCP inventory and tool input;
  credential environment filtering; instructor profile/hash checks; fixed command
  selection; readonly intent; model allowlist; generated configuration separation.
- **Passed with real Docker:** the credential-free worker runs as UID 65534;
  injected host sentinel and provider/GitHub credential variables are absent;
  workspace and system files reject writes; Docker socket is absent; outbound
  connection fails; unlisted command is rejected; deliberately failing candidate
  reports failure. See `tests/integration_worker.py` for the repeatable probe.
- **Passed with real Docker:** all twelve advertised non-intent practice entrypoints
  execute from actual starter directories. Every unfinished starter fails the
  expected exercise assertion. There were no missing-file/command failures.
- **Passed:** real CLI `--check` for preflight and intent validates 2.1.310 and
  required flags. Preflight exposes scoped patch/tests; intent has empty MCP.
  The temporary check configuration used `claude-sonnet-4-6`; this verifies only
  configuration construction, not model availability or observed provider identity.
- **Passed:** all eleven local assessment source/rubric inventories and CLI `--check`
  paths, including boolean questions, negation traps and exact-quote policies.
  A mocked Claude transport exercises report generation; no report is called
  authenticated provider evidence.
- **Passed with real Docker:** both recurring-regression fresh-task contract
  checks accept reference helper implementations and reject the obsolete-format
  regression. Actual Claude generation is separate and unrun.
- **Passed:** configuration-only checks for recurring, subagents, hooks, skills
  and the routing task pack (using a disposable completed route mapping). The
  pinned local binary contains the output-token and retry limit settings used by
  the route controller; actual provider behavior remains unrun.
- **Deferred by user choice:** live Claude assessment/rehearsal calls, interactive
  tool dispatch, actual native child sessions, skill activation and reported usage
  observations. There is no model-backed acceptance claim from transport mocks.
- **Unrun:** student onboarding, submissions through the App and persisted
  TensorLearn results.
- **Not claimed:** human authorship, authenticated local model identity or replay,
  independent CI model judgment, hard/account-wide financial caps, or invoice-grade
  accounting. All AI uses the student's local login; GitHub requires no AI key.

Worker image used:
`node:22.18.0-alpine@sha256:1b2479dd35a99687d6638f5976fd235e26c5b37e8122f786fcd5fe231d63de5b`.
The Docker probe establishes these tested behaviors on this host/image; it is not
a claim of exhaustive container-escape resistance or cohort readiness.
