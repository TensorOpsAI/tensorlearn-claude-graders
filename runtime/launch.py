#!/usr/bin/env python3
"""Launch a restricted local authoring session from the trusted grader release."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from core import Broker, IMAGE, InfrastructureError, PolicyError, ROOT, load_profile

CLAUDE_VERSION = "2.1.310"
FLAGS = ["--restricted", "--settings", "--strict-mcp-config", "--mcp-config", "--tools",
         "--model", "--permission-mode", "--permission-prompts", "--no-chrome"]


def configuration(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or set(value) != {"models", "workerImage"}:
        raise PolicyError("Instructor configuration requires only models and workerImage.")
    models = value["models"]
    if (not isinstance(models, list) or not models or len(models) > 10
            or any(not isinstance(m, str) or not re.fullmatch(r"claude-[a-z]+-[0-9][a-z0-9-]*", m)
                   or "latest" in m for m in models)):
        raise PolicyError("Instructor must configure full versioned Claude model IDs; aliases are rejected.")
    image = value["workerImage"]
    if image is not None and (not isinstance(image, str) or not IMAGE.fullmatch(image)):
        raise PolicyError("workerImage must be null or a digest-pinned Docker image.")
    return value


def check_cli(executable):
    version = subprocess.run([executable, "--version"], text=True, capture_output=True, timeout=15, check=True).stdout
    if not version.startswith(CLAUDE_VERSION + " "):
        raise InfrastructureError("This release requires Claude Code " + CLAUDE_VERSION + ".")
    help_text = subprocess.run([executable, "--help"], text=True, capture_output=True, timeout=15, check=True).stdout
    if any(flag not in help_text for flag in FLAGS):
        raise InfrastructureError("Claude CLI is missing a required restriction flag.")


def prepare(workspace, key, model, config, destination, executable):
    workspace, destination = Path(workspace).resolve(strict=True), Path(destination).resolve(strict=True)
    if ROOT.is_relative_to(workspace) or destination.is_relative_to(workspace):
        raise PolicyError("Runtime and generated configuration must be outside the learner workspace.")
    profile = load_profile(key)
    if model not in config["models"]:
        raise PolicyError("The selected model is not in the instructor configuration.")
    broker = Broker(workspace, profile, config["workerImage"])
    names = broker.tool_names()
    settings = {"permissions": {"allow": ["Read", "Glob", "Grep"] + ["mcp__lab__" + n for n in names],
                                "deny": ["Bash", "PowerShell", "Edit", "Write", "Agent", "WebFetch", "WebSearch"]}}
    servers = {}
    if names:
        server_args = ["-I", str(ROOT / "server.py"), "--workspace", str(workspace), "--exercise", key]
        if config["workerImage"]:
            server_args += ["--image", config["workerImage"]]
        servers["lab"] = {"command": sys.executable, "args": server_args}
    settings_path, mcp_path = destination / "settings.json", destination / "mcp.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    mcp_path.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
    settings_path.chmod(0o400)
    mcp_path.chmod(0o400)
    command = [executable, "--restricted", "--settings", str(settings_path), "--strict-mcp-config",
               "--mcp-config", str(mcp_path), "--tools", "Read,Glob,Grep", "--model", model,
               "--permission-mode", "dontAsk", "--no-chrome", "--append-system-prompt", profile["coach"]]
    return command, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="One exercise directory, not the entire repository")
    parser.add_argument("--exercise", required=True)
    parser.add_argument("--configuration", required=True, help="Instructor-owned model/image JSON outside learner checkout")
    parser.add_argument("--model", required=True)
    parser.add_argument("--check", action="store_true", help="Validate configuration and CLI without making model calls")
    parser.add_argument("--mode", choices=["author", "replay"], default="author")
    args = parser.parse_args()
    if args.mode != "author":
        raise InfrastructureError("Use the exercise-specific replay.py, behavior_replay.py or simulate.py command for a local rehearsal; launch.py only authors files.")
    workspace = Path(args.workspace).resolve(strict=True)
    if Path(args.configuration).resolve(strict=True).is_relative_to(workspace):
        raise PolicyError("Instructor configuration cannot come from the learner exercise.")
    config = configuration(args.configuration)
    executable = shutil.which("claude")
    if not executable:
        raise InfrastructureError("Install the instructor-pinned Claude CLI first.")
    check_cli(executable)
    with tempfile.TemporaryDirectory(prefix="tensorlearn-authoring-") as temporary:
        command, names = prepare(workspace, args.exercise, args.model, config, temporary, executable)
        if args.check:
            print(json.dumps({"mode": "author", "exercise": args.exercise, "claudeVersion": CLAUDE_VERSION,
                              "builtins": ["Read", "Glob", "Grep"], "mcpTools": names,
                              "model": args.model, "practiceImageConfigured": bool(config["workerImage"]),
                              "modelCallsMade": False, "replayAvailable": False}))
            return
        env = dict(os.environ)
        # Remove flags inherited from a different session that would suppress project guidance.
        for key in ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"):
            env.pop(key, None)
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        raise SystemExit(subprocess.call(command, cwd=workspace, env=env))


if __name__ == "__main__":
    try:
        main()
    except (PolicyError, InfrastructureError, subprocess.SubprocessError, OSError, ValueError) as error:
        print("Authoring launcher unavailable: " + str(error), file=sys.stderr)
        raise SystemExit(2)
