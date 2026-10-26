#!/usr/bin/env python3
"""Run two fresh local Claude sessions for the recurring-regression exercise.

The student's login pays for these calls. Output is a local rehearsal trace, not
an authenticated grading statement, and is kept outside the submitted exercise.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assess import InfrastructureError, PolicyError, run_claude
from core import Broker, ROOT, load_profile, verify_release
from launch import check_cli, configuration, prepare

TASKS = {
    "format": "Add an exported isCustomerCode(value) helper in the customer-code module. It returns true exactly when normalizeCustomerCode accepts the value; otherwise return false, including non-string values. Keep existing callers working, add useful regression coverage and run the available checks.",
    "status": "Extend the object returned by createClaimsClient with async getStatusLabel(customerCode). It returns the status field as text for the portal display. Keep get(customerCode) and the project's existing behavior working for other callers. Add useful regression coverage and run the available checks."
}


def hashes(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise PolicyError("Replay files must not contain symlinks.")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def parse_events(raw):
    events = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError as error:
            raise InfrastructureError("Claude replay did not return valid stream JSON.") from error
        if not isinstance(event, dict):
            raise InfrastructureError("Claude replay event is malformed.")
        events.append(event)
    finals = [event for event in events if event.get("type") == "result"]
    if not finals or finals[-1].get("is_error") or finals[-1].get("subtype") != "success":
        raise InfrastructureError("Claude replay did not complete successfully. No passing rehearsal was recorded.")
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--exercise", choices=["recurring-regressions"], default="recurring-regressions")
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, help="New trace directory outside the learner checkout")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    workspace, output = Path(args.workspace).resolve(strict=True), Path(args.output).resolve()
    if output.is_relative_to(workspace) or ROOT.is_relative_to(workspace):
        raise PolicyError("Replay runtime and trace directory must be outside the learner workspace.")
    if Path(args.configuration).resolve(strict=True).is_relative_to(workspace):
        raise PolicyError("Instructor configuration must be outside the learner workspace.")
    if output.exists():
        raise PolicyError("Choose a new output directory so earlier traces are preserved.")
    config = configuration(args.configuration)
    if args.model not in config["models"] or not config["workerImage"]:
        raise PolicyError("Replay needs an approved model and pinned practice image.")
    executable = shutil.which("claude")
    if not executable:
        raise InfrastructureError("Install the pinned CLI and sign in with your own Claude account.")
    check_cli(executable)
    verify_release()
    profile = load_profile(args.exercise)
    original = Broker(workspace, profile, config["workerImage"])
    if args.check:
        with tempfile.TemporaryDirectory(prefix="tensorlearn-replay-check-") as tmp:
            original._snapshot(Path(tmp) / "source")
        print(json.dumps({"exercise": args.exercise, "tasks": list(TASKS), "modelCallsMade": False,
                          "normalProjectGuidance": True, "autoMemory": False,
                          "officialGradeCreated": False}))
        return
    output.mkdir(parents=True, mode=0o700)
    results = []
    initial = hashes(workspace)
    for name, prompt in TASKS.items():
        with tempfile.TemporaryDirectory(prefix="tensorlearn-fresh-session-") as tmp:
            root = Path(tmp)
            candidate, settings = root / "candidate", root / "settings"
            original._snapshot(candidate)
            settings.mkdir(mode=0o700)
            before = hashes(candidate)
            command, tools = prepare(candidate, args.exercise, args.model, config, settings, executable)
            command += ["--print", "--verbose", "--output-format", "stream-json", "--include-hook-events",
                        "--permission-prompts", "none", "--no-session-persistence"]
            # prepare deliberately does not pass --safe-mode/--bare: project guidance is
            # the subject of this exercise. Candidate hooks/settings remain ignored by --restricted.
            raw = run_claude(command, prompt, candidate, timeout=240, output_limit=2 * 1024 * 1024)
            events = parse_events(raw)
            (output / (name + ".jsonl")).write_text(raw)
            after = hashes(candidate)
            changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
            check = Broker(candidate, profile, config["workerImage"]).run_trusted_check(name)
            result = {"task": name, "prompt": prompt, "model": args.model, "sessionResult": "success",
                      "eventCount": len(events), "availableMcpTools": tools,
                      "changedFiles": changed, "resultingHashes": after, "functionalCheck": check}
            results.append(result)
            (output / (name + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
    if hashes(workspace) != initial:
        raise PolicyError("Original sources changed during rehearsal. Discard these observations and rerun.")
    summary = {"schema": 1, "kind": "local-rehearsal", "exercise": args.exercise,
               "sourceHashes": initial, "modelDeclared": args.model, "tasks": results,
               "passed": all(result["functionalCheck"]["passed"] for result in results),
               "officialGradeCreated": False,
               "limits": "Learner-controlled local trace; no authenticated model identity, human authorship or CI replay attestation."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"traceDirectory": str(output), "localRehearsalPassed": summary["passed"], "officialGradeCreated": False}))


if __name__ == "__main__":
    try:
        main()
    except (PolicyError, InfrastructureError, OSError, ValueError) as error:
        print("Local replay unavailable: " + str(error), file=sys.stderr)
        raise SystemExit(2)
