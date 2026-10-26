"""Instructor release step; run before committing the pinned grader release."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
rubrics = root.parent / "rubrics"
rubric_lock = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(rubrics.glob("*.json"))}
(root / "rubric-lock.json").write_text(json.dumps(rubric_lock, indent=2) + "\n")
names = ["core.py", "server.py", "launch.py", "profiles.json", "assess.py", "rubric-lock.json",
         "replay.py", "route.py", "simulate.py", "simulation_server.py", "behavior_replay.py", "behavior_server.py"]
names += [p.relative_to(root).as_posix() for p in sorted((root / "practice").glob("*.mjs"))]
names += [p.relative_to(root).as_posix() for p in sorted((root / "checks").glob("*.mjs"))]
record = {"schema": 1, "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}}
(root / "release.json").write_text(json.dumps(record, indent=2) + "\n")
