"""Shared test fixtures: a throwaway engine home copied from examples/home, and a fake `claude`."""

from __future__ import annotations

import dataclasses
import shutil
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path

from growth.config import Engine, load_engine
from growth.store import Store

REPO = Path(__file__).resolve().parent.parent
EXAMPLE_HOME = REPO / "examples" / "home"
NOW = datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc)  # 04:00 in Berlin, inside the example's AI window

FAKE_CLAUDE = textwrap.dedent(
    """
    import json, os, sys
    prompt = sys.stdin.read()
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "keys": sorted(k for k in os.environ if k.startswith("ANTHROPIC"))}) + "\\n")
    if mode == "limit":
        print(json.dumps({"result": "Claude usage limit reached. Your limit will reset at 5pm.", "is_error": True}))
        sys.exit(1)
    if mode == "directories":
        ids = [line[2:].split(" | ")[0] for line in prompt.splitlines() if line.startswith("- ") and " | " in line]
        listings = {i: {"tagline": "Your plugins in any browser.", "description": "Run the Windows audio plugins you own in a browser tab. Free during the beta."} for i in ids}
        if ids:
            listings[ids[0]] = {"tagline": "Loved by 12000 producers.", "description": "Invented."}
        print(json.dumps({"result": "```json\\n" + json.dumps(listings) + "\\n```", "is_error": False}))
        sys.exit(0)
    if mode == "invent":
        print(json.dumps({"result": "Traffic grew to 48213 visitors.", "is_error": False}))
        sys.exit(0)
    print(json.dumps({"result": "## Kiln\\nEverything ran. Three changes: keep going.", "is_error": False}))
    """
)


class HomeTestCase(unittest.TestCase):
    """Each test gets its own copy of the example home, with state and output inside it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="growth-test-"))
        self.home = self.tmp / "home"
        shutil.copytree(EXAMPLE_HOME, self.home, ignore=shutil.ignore_patterns("state", "dist"))
        self.project_dir = self.home / "projects" / "example"
        fake = self.tmp / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE)
        self.fake_claude = [sys.executable, str(fake)]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **ai_overrides) -> Engine:
        engine = load_engine(self.home, state_dir=self.home / "state", dist_dir=self.home / "dist")
        engine.ai = dataclasses.replace(engine.ai, command=self.fake_claude, **ai_overrides)
        return engine

    def store(self, engine: Engine) -> Store:
        store = Store(engine.state_dir / "engine.db")
        self.addCleanup(store.close)
        return store

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.home / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"{rel} does not contain {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def only_core_jobs(self) -> None:
        """Switch off the example's launch-directory jobs, leaving fetch-data, build-site and the digest."""
        for kind in ("directories-sync", "directories-draft", "directories-submit"):
            self.edit("projects/example/project.toml", f'kind = "{kind}"', f'kind = "{kind}"\nenabled = false')

    def append(self, rel: str, text: str) -> None:
        path = self.home / rel
        path.write_text(path.read_text(encoding="utf-8") + "\n" + textwrap.dedent(text), encoding="utf-8")
