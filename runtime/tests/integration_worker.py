"""Explicit Docker acceptance probe. Run separately; not part of mocked unit tests."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Broker, PolicyError

IMAGE = "node:22.18.0-alpine@sha256:1b2479dd35a99687d6638f5976fd235e26c5b37e8122f786fcd5fe231d63de5b"
PROBE = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
assert.equal(process.getuid(), 65534);
assert.equal(process.env.TENSORLEARN_PROBE_SECRET, undefined);
assert.equal(process.env.GITHUB_TOKEN, undefined);
assert.equal(process.env.ANTHROPIC_API_KEY, undefined);
assert.throws(() => fs.writeFileSync('/workspace/changed', 'x'));
assert.throws(() => fs.writeFileSync('/etc/changed', 'x'));
assert.equal(fs.existsSync('/var/run/docker.sock'), false);
const connected = await new Promise(resolve => {
  const s = net.connect({host:'198.51.100.1',port:443});
  s.on('connect', () => {s.destroy(); resolve(true);});
  s.on('error', () => resolve(false));
  s.setTimeout(500, () => {s.destroy(); resolve(false);});
});
assert.equal(connected,false);
console.log('PASS uid, secret filtering, read-only mounts, no socket, no network');
"""


def main():
    with tempfile.TemporaryDirectory(prefix="lab-runtime-acceptance-") as directory:
        root = Path(directory)
        (root / "probe.mjs").write_text(PROBE)
        profile = {"writeAllow": ["answer.json"], "writeDeny": [], "tests": {"probe": ["node", "probe.mjs"]}, "coach": "test"}
        broker = Broker(root, profile, IMAGE)
        os.environ["TENSORLEARN_PROBE_SECRET"] = "sentinel-never-forward-to-worker"
        result = broker.run_tests("probe")
        assert result["passed"], result
        try:
            broker.run_tests("node -e process.env")
        except PolicyError:
            pass
        else:
            raise AssertionError("Unlisted command was accepted")
        (root / "probe.mjs").write_text("throw new Error('expected learner failure')")
        failed = broker.run_tests("probe")
        assert failed["exitCode"] != 0 and not failed["passed"]
        print(json.dumps({"workerIsolationProbe": "passed", "unlistedCommand": "denied", "failingPractice": "reported-failed", "image": IMAGE, "officialGradeCreated": False}))


if __name__ == "__main__":
    main()
