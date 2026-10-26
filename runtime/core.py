"""Instructor-owned authoring capabilities; candidate programs never execute here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
MAX_FILE = 65536
MAX_OUTPUT = 65536
MAX_SNAPSHOT = 5 * 1024 * 1024
IMAGE = re.compile(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}\Z")
INFRA = [".git", ".git/**", ".github", ".github/**", ".mcp.json", ".claude/settings.json",
         ".claude/settings.local.json", "exercise.json", "runtime", "runtime/**", "grader", "grader/**"]


class PolicyError(ValueError):
    pass


class InfrastructureError(RuntimeError):
    pass


def relative_name(value):
    if not isinstance(value, str) or not value or len(value) > 300:
        raise PolicyError("Use a bounded relative file path.")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts) or "\\" in value or "\x00" in value:
        raise PolicyError("Absolute paths, path escapes and ambiguous separators are forbidden.")
    if PurePosixPath(value).is_absolute() or any(ord(c) < 32 for c in value):
        raise PolicyError("Invalid relative path.")
    return parts


def matches(name, pattern):
    expression = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(expression, name) is not None


def permitted(name, profile):
    relative_name(name)
    return (any(matches(name, p) for p in profile["writeAllow"])
            and not any(matches(name.casefold(), p.casefold()) for p in INFRA + profile.get("writeDeny", [])))


def clean_env():
    # Docker configuration/auth and Claude/GitHub/provider credentials are absent.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin", "LANG": "C.UTF-8"}


def verify_release(root=ROOT):
    root = Path(root).resolve(strict=True)
    lock = json.loads((root / "release.json").read_text())
    if set(lock) != {"schema", "files"} or lock["schema"] != 1 or not isinstance(lock["files"], dict):
        raise PolicyError("Malformed instructor release lock.")
    required = {"core.py", "server.py", "launch.py", "profiles.json", "assess.py", "rubric-lock.json",
                "replay.py", "route.py", "simulate.py", "simulation_server.py", "behavior_replay.py", "behavior_server.py"}
    if not required <= lock["files"].keys():
        raise PolicyError("Incomplete instructor release lock.")
    for name, digest in lock["files"].items():
        relative_name(name)
        path = root.joinpath(name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise PolicyError("Instructor release contains an unsafe path.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PolicyError("Instructor release hash mismatch: " + name)
    return lock


def load_profile(key, root=ROOT):
    verify_release(root)
    profiles = json.loads((Path(root) / "profiles.json").read_text())
    if key not in profiles:
        raise PolicyError("Unknown instructor exercise profile.")
    p = profiles[key]
    if set(p) != {"writeAllow", "writeDeny", "tests", "coach"}:
        raise PolicyError("Unsupported instructor profile field.")
    for name in p["writeAllow"] + p["writeDeny"]:
        if name != "**":
            relative_name(name[:-3] if name.endswith("/**") else name)
    for test, argv in p["tests"].items():
        if not re.fullmatch(r"[a-z0-9.-]{1,80}", test) or not isinstance(argv, list) or not argv:
            raise PolicyError("Invalid fixed test ID/command.")
        if argv[0] != "node" or any(not isinstance(v, str) or len(v) > 300 for v in argv):
            raise PolicyError("Only instructor-listed Node commands are supported.")
    if key == "intent" and (p["writeAllow"] or p["tests"]):
        raise PolicyError("The intent coach must remain read-only.")
    return p


class Broker:
    def __init__(self, workspace, profile, image=None, docker="docker"):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise PolicyError("Workspace must be a directory.")
        self.profile, self.image, self.docker = profile, image, docker

    def tool_names(self):
        return (["apply_patch"] if self.profile["writeAllow"] else []) + (["run_tests"] if self.profile["tests"] else [])

    def apply_patch(self, path, content):
        """Atomic full-file replacement; no shell patch language or deletion operation."""
        parts = relative_name(path)
        if not permitted(path, self.profile):
            raise PolicyError("This exercise does not permit editing that file.")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE:
            raise PolicyError("Replacement must be UTF-8 text of at most 64 KiB.")
        fd = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        tempname = None
        try:
            for part in parts[:-1]:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nextfd
            try:
                existing = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode):
                    raise PolicyError("The target must be a regular file, never a symlink.")
            except FileNotFoundError:
                pass
            tempname = ".lab-write-" + secrets.token_hex(12)
            out = os.open(tempname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            with os.fdopen(out, "wb") as stream:
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tempname, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
            tempname = None
            return {"path": path, "bytes": len(content.encode("utf-8")), "sha256": hashlib.sha256(content.encode()).hexdigest()}
        except OSError as error:
            raise PolicyError("Unsafe or inaccessible workspace path.") from error
        finally:
            if tempname:
                os.unlink(tempname, dir_fd=fd)
            os.close(fd)

    def run_tests(self, test_id):
        if test_id not in self.profile["tests"]:
            raise PolicyError("Unknown fixed test ID. Shell commands are not accepted.")
        return self._run_worker(self.profile["tests"][test_id], test_id)

    def run_trusted_check(self, task):
        """Controller-only fresh-session contract check, never an exported MCP operation."""
        if task not in ("format", "status"):
            raise PolicyError("Unknown instructor fresh-session task.")
        verify_release()
        check_file = ROOT / "checks" / "recurring.mjs"
        if check_file.is_symlink() or not check_file.is_file():
            raise PolicyError("Missing instructor fresh-session check.")
        return self._run_worker(["node", "/checks/recurring.mjs", task], "recurring." + task, check_file)

    def _run_worker(self, argv, test_id, check_file=None):
        if not self.image or not IMAGE.fullmatch(self.image):
            raise InfrastructureError("Practice requires an instructor-configured digest-pinned Docker image.")
        try:
            check = subprocess.run([self.docker, "image", "inspect", self.image], env=clean_env(),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InfrastructureError("Docker is unavailable; no candidate code was run.") from error
        if check.returncode:
            raise InfrastructureError("Docker or the pinned image is unavailable. Preload the reviewed image separately.")
        with tempfile.TemporaryDirectory(prefix="tensorlearn-practice-") as scratch:
            snapshot = Path(scratch) / "workspace"
            self._snapshot(snapshot)
            name = "tensorlearn-practice-" + secrets.token_hex(12)
            command = [self.docker, "run", "--rm", "--pull=never", "--name", name,
                       "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                       "--user=65534:65534", "--pids-limit=64", "--memory=256m", "--cpus=1",
                       "--ulimit=nofile=128:128", "--ulimit=fsize=1024:1024",
                       "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m", "--workdir=/workspace",
                       "--mount", f"type=bind,src={snapshot},dst=/workspace,readonly",
                       "--env=HOME=/tmp", "--env=NODE_OPTIONS="]
            if check_file is not None:
                command += ["--mount", f"type=bind,src={check_file},dst=/checks/recurring.mjs,readonly"]
            command += [self.image] + argv
            try:
                result = bounded_process(command, timeout=20)
                return {"testId": test_id, **result, "officialScore": False}
            finally:
                try:
                    subprocess.run([self.docker, "rm", "-f", name], env=clean_env(),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    raise InfrastructureError("Docker cleanup could not be confirmed; inspect the named practice container.")

    def _snapshot(self, destination):
        destination.mkdir(mode=0o755)
        total = count = 0
        for directory, dirs, files, dirfd in os.fwalk(self.workspace, follow_symlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in dirs + files:
                source = Path(directory) / name
                relative = source.relative_to(self.workspace)
                info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise PolicyError("Remove symlinks before running practice checks.")
                target = destination / relative
                if stat.S_ISDIR(info.st_mode):
                    target.mkdir(mode=0o755, exist_ok=True)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise PolicyError("Practice only accepts ordinary files.")
                total += info.st_size
                count += 1
                if total > MAX_SNAPSHOT or count > 2000 or info.st_size > MAX_FILE:
                    raise PolicyError("Practice workspace exceeds the bounded snapshot size.")
                # Directory-relative access prevents concurrent parent symlink replacement.
                with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd), "rb") as data:
                    opened = os.fstat(data.fileno())
                    if not stat.S_ISREG(opened.st_mode) or opened.st_ino != info.st_ino or opened.st_dev != info.st_dev:
                        raise PolicyError("Workspace changed during snapshot.")
                    content = data.read(MAX_FILE + 1)
                if len(content) > MAX_FILE:
                    raise PolicyError("File grew beyond the practice size limit.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o644)


def bounded_process(command, timeout):
    process = subprocess.Popen(command, env=clean_env(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InfrastructureError("Practice worker timed out; no official score was produced.")
            for key, _ in selector.select(min(remaining, .2)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > MAX_OUTPUT:
                    raise InfrastructureError("Practice worker exceeded its output limit.")
        code = process.wait(timeout=max(.1, deadline-time.monotonic()))
        return {"exitCode": code, "passed": code == 0, "output": output.decode("utf-8", errors="replace")}
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
