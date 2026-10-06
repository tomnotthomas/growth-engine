"""Runs the waitlist Worker's JavaScript tests (tests/worker/) when Node 22.5+ is installed."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def node_with_sqlite() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    probe = subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True, text=True)
    return node if probe.returncode == 0 else None


@unittest.skipUnless(node_with_sqlite(), "needs Node 22.5+ with node:sqlite")
class WaitlistWorker(unittest.TestCase):
    def test_worker_suite(self) -> None:
        files = sorted(str(p) for p in (REPO / "tests" / "worker").glob("*.test.mjs"))
        proc = subprocess.run([node_with_sqlite(), "--test", *files], capture_output=True, text=True, cwd=REPO, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])
